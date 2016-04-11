from django.conf import settings
from django.contrib import admin
from django.core.files.uploadedfile import UploadedFile
from django.db import models
from django.utils import timezone
from sculpt.common import Enumeration, EnumerationData
from sculpt.common.parameter_proxy import parameter_proxy
from sculpt.model_tools.base import AbstractAutoHash
from sculpt.model_tools.mixins import OverridableChoicesMixin
from sculpt.s3files.process_images import process_image, RESIZE_MODES, ANCHOR_HORIZONTAL, ANCHOR_VERTICAL
import datetime
import os

# S3 Stored File base model
#
# We need to store many files that we intend to
# serve via Amazon S3 rather than run the web
# site infrastructure ourselves and serve static
# files via nginx. We also want to track metadata
# for these files and be able to create thumbnail
# versions of some of these files. Rather than
# create separate models for each category of
# file, we use a single model with conditionally-
# populated fields.
#
# This is an abstract base class. You must create
# an actual implementation of this class that will
# likely contain a link to the AppUser class so
# uploaded files can be tracked per user.
#
# If you want automatic derived-image processing,
# override the DERIVATION_TYPES enumeration and
# set up your actual derived sizes, along with
# when that derivation should be applied.
#
class AbstractStoredFile(OverridableChoicesMixin, AbstractAutoHash):

    # hash
    # NOTE: you should probably override the secret
    # in your derived class
    AUTOHASH_SECRET = 'vr789354VIYBTbgriou$%VF()FBG45biu$%R&I($^f823evjH4vf37844WVFUjk%ULOG9874f(V%#E*6evy'
    AUTOHASH_FIELDS = [ 'original_filename', 'size', 'mime_type', ]
    
    # file metadata
    original_filename = models.CharField(max_length = 256, blank = True, null = True)   # from the client's system, if available; mainly used for pretty names for the user
    size = models.IntegerField(blank = True, null = True)                               # null only if unknown (rare)
    width = models.IntegerField(blank = True, null = True)                              # if image or video type
    height = models.IntegerField(blank = True, null = True)                             # if image or video type
    duration = models.DecimalField(blank = True, null = True, max_digits = 10, decimal_places = 3)  # if audio or video type; length of time, in seconds
    mime_type = models.CharField(max_length = 128, blank = True, null = True)           # long field because Microsoft uses long MIME types
    # NOTE: mime_type comes from the client and thus we assume is not trustworthy

    # at the time we create the file, we generate a path
    # that includes elements of the hash; however, it's
    # possible we might change the generation parameters
    # as a site grows, so we record explicitly what the
    # path is to aid in later migrations
    generated_filename = models.CharField(max_length = 128, blank = True, null = True)

    # for certain file types, we will attempt to open the
    # file and extract some additional metadata from it
    # (dimensions, duration); if this fails, this flag will
    # be set to False so we know the file is corrupt
    #
    # NOTE: for files which are NOT parsed, this will be
    # set to None, NOT True, because we do not know; this
    # allows the application to override the check_validity
    # method.
    #
    # NOTE: if there is an error writing the file contents
    # to disk, this will immediately be set to False.
    #
    is_valid = models.NullBooleanField(default = None, blank = True, null = True)

    # has this been copied to S3 yet?
    REMOTE_STATUS = Enumeration(
            (-1, 'LOCAL_CORRUPT'),      # this file appears to be incorrectly saved on the local server
            ( 0, 'LOCAL_INCOMPLETE'),   # this is still incomplete on the local server (so should not be copied)
            ( 1, 'LOCAL_ONLY'),         # this is on the local server and should not be copied (but is complete)
            ( 2, 'LOCAL_READY'),        # this is ready to be copied
            ( 3, 'IN_PROGRESS'),        # this is being copied to S3
            ( 4, 'REMOTE_ONLY'),        # this is completed on S3 and is not present locally
        )
    remote_status = models.IntegerField(choices = REMOTE_STATUS.choices, default = REMOTE_STATUS.LOCAL_INCOMPLETE)

    # is this a thumbnail/derived version of another file?
    # NOTE: we explicitly link this to self so that if you
    # create multiple derived classes, they will all work
    # (but you probably shouldn't do that)
    derived_from = models.ForeignKey('self', related_name = 'derivations', blank = True, null = True)
    
    # possible choices for derived files; we enumerate
    # these so that we can be consistent in how they are
    # applied, and also so that we can automatically
    # create some of these at file upload time
    #
    DERIVATION_MODES = Enumeration(
            (0, 'MANUAL'),      # require manual creation
            (1, 'LAZY'),        # create as soon as it's requested
            (2, 'IMMEDIATELY'), # create when file is uploaded
        )
    DERIVATION_TYPES = Enumeration(
            labels = ('value','id','label','mode','operations'),
            choices = (
                (0, 'THUMBNAIL', 'Thumbnail', DERIVATION_MODES.IMMEDIATELY,
                    [{
                        'operation': 'resize',
                        'target_size': (50,50),
                        'resize_mode': RESIZE_MODES.CROP,
                        'anchor_horizontal': ANCHOR_HORIZONTAL.CENTER,
                        'anchor_vertical': ANCHOR_VERTICAL.CENTER,
                        'background_color': None,
                    }]),
            )
        )
    derivation_type = models.IntegerField(choices = DERIVATION_TYPES.choices, blank = True, null = True)    # null for complete/original file
    derivation_type_data = property(EnumerationData('DERIVATION_TYPES', 'derivation_type'))

    # when was this created and/or stored in S3?
    date_created = models.DateTimeField()
    date_stored = models.DateTimeField(blank = True, null = True)
    
    # Files are uploaded and processed before the thing
    # they ultimately belong to is finalized; this means
    # there is a workflow where the user abandons the file
    # part-way through the process. We need to be able to
    # clean up these files in a generic way, so we include
    # the option to mark a file as auto-expiring; when the
    # user commits the action that claims the file, this
    # field can then be cleared and the file preserved.
    #
    # NOTE: this process is independent of whether it is
    # copied to S3 or not; a file may get fully copied
    # to S3 and then still discarded.
    #
    date_expires = models.DateTimeField(blank = True, null = True)

    # additional fields are primarily foreign key links
    # to other records; however in many instances you will
    # find that a better pattern is to create an intermediate
    # record that contains additional metadata (title,
    # description, thumbnail processing choices, etc.) that
    # links back to your other models, and contains a link
    # to a StoredFile object
    #
    # user = models.ForeignKey('appuser.AppUser', related_name = 'stored_files', blank = True, null = True)    

    # metadata / restrictions
    class Meta:
        abstract = True

    # a debugging string cast
    # NOTE: ONLY use this for debugging, never in user-
    # facing code
    def __unicode__(self):
        return u"[%(class)s:%(id)s] %(short_hash)s %(size)s %(width)s x %(height)s %(mime_type)s %(remote_status)s" % {
                'class': self.__class__.__name__,
                'id': unicode(self.id),                             # so that None will not crash
                'short_hash': self.hash[:8] if self.hash else '-',
                'size': unicode(self.size if self.size is not None else '-'),
                'width': unicode(self.width if self.width is not None else '-'),
                'height': unicode(self.height if self.height is not None else '-'),
                'mime_type': unicode(self.mime_type if self.mime_type is not None else '-'),
                'remote_status': self.get_remote_status_display(),
            }

    # because the derived class will override the enumeration
    # for derivation_type, we want to update it on creation
    def __init__(self, *args, **kwargs):
        super(AbstractStoredFile, self).__init__(*args, **kwargs)
        self._set_field_choices(field_name = 'derivation_type', choices = self.DERIVATION_TYPES.choices)

    # we want to make sure generated_filename gets filled in
    # before we save; it's usually going to be set as soon as
    # the object is created (by the creating code)
    def save(self, *args, **kwargs):
        if self.generated_filename is None or self.generated_filename == '':
            self.generated_filename = self.generate_filename()

        # pass through to the regular save method
        return super(AbstractStoredFile, self).save(*args, **kwargs)

    # when we delete the record, we want to go ahead and clean
    # up any on-disk files, too
    #
    # Django allows us to override the delete() method but
    # this has some limitations. When performing bulk deletes
    # or cascaded deletes, this method will NOT be called so
    # there is always the chance that records could be deleted
    # and we are left with orphaned files. We can clean those
    # up after the fact, but it would be better to avoid those
    # situations entirely.
    #
    def delete(self):
        # first, delete all the derivations; we do this
        # one at a time so that we invoke the delete()
        # method on each derivation
        for d in self.derivations.all():
            d.delete()

        if os.path.exists(self.local_path):
            os.remove(self.local_path)

        return super(AbstractStoredFile, self).delete()

    #
    # utility functions
    #
    
    # determine whether a file is ready for access
    @property
    def is_ready(self):
        return self.is_valid and self.remote_status in [ REMOTE_STATUS.LOCAL_ONLY, REMOTE_STATUS.REMOTE_ONLY ]

    # file type tests
    #
    # strictly speaking, we could determine these by just checking
    # the first part of the MIME type, but practically we are
    # asking a much narrower question, which is whether the file
    # is of a type within that category that can be processed by
    # our code
    
    @property
    def is_image(self):
        return self.mime_type in [ 'image/gif', 'image/jpeg', 'image/png', ]    # not included: Windows BMP, TIFF
        
    @property
    def is_video(self):
        return self.mime_type in [ 'video/mpeg', 'video/webm', 'video/x-flv', ] # not included: Windows Media
        
    @property
    def is_audio(self):
        return self.mime_type in [ 'audio/mpeg', 'audio/x-wav', ]               # not included: RealAudio, AIFF, Windows Media

    # return a response that will serve the file contents; note
    # this ALWAYS serves via the internal URL; if you are sure
    # the file does not require access controls and is publicly-
    # accessible via the web server, return a permanent redirect
    # response instead or, better yet, write the correct public
    # URL in the referring page
    #
    # NOTE: you can't use this as an AJAX response.
    #
    def as_response(self, request):
        if settings.SCULPT_S3FILES_REMOTE_MODE == 'local':
            from django.http.response import HttpResponse

            # we're going to pull the MIME type from our database
            # but it's very important that this MIME type be
            # validated by the application before this happens;
            # do not accept arbitrary MIME types without some kind
            # of sanity checking
            response = HttpResponse(content_type = self.mime_type)
            if settings.SCULPT_S3FILES_SERVER_TYPE == 'nginx':
                response['X-Accel-Redirect'] = settings.SCULPT_S3FILES_INTERNAL_URL + self.generated_filename
            if settings.SCULPT_S3FILES_DUMP_RESPONSES:
                print str(response)

            return response

    # a common use case is needing to create a stored file
    # based on the results of an urllib2 request; this will do
    # that, applying any additional parameters given to the
    # creation, and setting image dimensions if the file is
    # an image
    #
    # NOTE: this is a factory method
    #
    # NOTE: this won't fill in an expiration date; it's assumed
    # that you will provide one (you can use default_date_expires())
    #
    @classmethod
    def create_from_http_response(cls, response, attrs = None):

        # determine the original filename, as plucked from
        # the request; note that this is always 
        import urlparse
        parsed_url = urlparse.urlparse(response.geturl())
        basename = os.path.basename(parsed_url.path)
        if basename == '':
            # bare directory requested, use the last directory name instead
            basename = parsed_url.path.rsplit('/',2)[-2]
            if basename == '':
                # must've requested the root path
                basename = '_unknown_'

        # base attributes can be overridden
        base_attrs = {
                'original_filename': basename,
                'date_created': datetime.datetime.utcnow(),
            }
        if attrs is not None:
            base_attrs.update(attrs)
        
        # remaining attributes are always populated from
        # the response
        base_attrs.update({
                'size': response.info().getheader('Content-Length'),
                'mime_type': response.info().gettype(),
            })

        # create, but don't save, the object
        sf = cls(**base_attrs)

        # attempt to write the file; this will generate a
        # hash and local filename
        sf.write_to_disk(response, save = False)

        # try to extract image dimensions
        original_image = None
        if settings.SCULPT_S3FILES_CHECK_IMAGES and sf.is_image:
            from PIL import Image
            try:
                original_image = Image.open(sf.local_path)

                # valid image, extract metadata            
                sf.width = original_image.size[0]
                sf.height = original_image.size[1]
                sf.is_valid = True
            
            except IOError, e:
                # the file seems to be invalid
                sf.is_valid = False

        # we're done parsing the file; save the meta data
        # record to generate its hash and local file path
        sf.save()

        # see if we need to create any derived images
        if sf.is_valid and original_image:
            for derivation in cls.DERIVATION_TYPES.iter_dicts():
                if derivation['mode'] == cls.DERIVATION_MODES.IMMEDIATELY:
                    # this is a process we need to do now, but pass the
                    # original image we have so it won't be re-created
                    sf.generate_derivation(derivation['id'], original_image)

        # give back the new record
        return sf

    # if you're truly lazy, we'll do the request for you,
    # too (but we'll raise an exception if it fails)
    @classmethod
    def create_from_url(cls, url, attrs = None):
        import urllib2
        response = urllib2.urlopen(url)
        return cls.create_from_http_response(response, attrs)

    # generate a relative path to where the data is stored,
    # including breaking up filenames based on parts of its
    # hash
    #
    # NOTE: if you change a hash, you must call this again to
    # generate a new filename
    #
    def generate_filename(self):

        # we absolutely must have a hash before we can generate
        # a local filename
        if self.hash is None or self.hash == '':
            self.generate_hash()

        # generate necessary path pieces
        #
        # NOTE: for small sites it's possible we are configured
        # to generate no path components; this is not an error,
        # although it's ill-advised
        #
        dirs = []
        for i in range(settings.SCULPT_S3FILES_SPLIT_LEVELS):
            dirs.append(self.hash[i*settings.SCULPT_S3FILES_SPLIT_CHARS:(i+1)*settings.SCULPT_S3FILES_SPLIT_CHARS])

        # the final element will copy the original file's
        # extension, which may be corrupted if the user is
        # doing dumb things; this should be tested more thoroughly
        # for systems which are prone to supplying bad extensions
        # (Mac)
        #
        root, ext = os.path.splitext(self.original_filename)
        dirs.append(self.hash[settings.SCULPT_S3FILES_SPLIT_LEVELS*settings.SCULPT_S3FILES_SPLIT_CHARS:] + ext)

        return os.path.join(*dirs)

    # get a local path for this file (assuming the file
    # is actually local, this is where it *should* be)
    #
    # NOTE: this is a good candidate for overriding in your
    # derived class, if you have protected files; you do not
    # want to write these to the same storage area as your
    # public files, as then anyone who can guess the file's
    # hash (or knows it from a previous visit) can fetch the
    # file without routing through code.
    #
    @property
    def local_path(self):
        return os.path.join(settings.SCULPT_S3FILES_LOCAL_DIR, self.generated_filename)

    # get internal URL for this file
    @property
    def internal_url(self):
        return settings.SCULPT_S3FILES_INTERNAL_URL + self.generated_filename

    # get external URL for this file
    #
    # NOTE: this is a good candidate for overriding in your
    # derived class, if you have protected files; do whatever
    # tests you need to determine the file is in the protected
    # group and generate a URL with the appropriate base
    #
    @property
    def external_url(self):
        return settings.SCULPT_S3FILES_EXTERNAL_URL + self.generated_filename

    # ensure the directories required for a file exist
    # on the local filesystem
    def ensure_local_path_exists(self):
        containing_dir = os.path.dirname(self.local_path) + os.sep
        if not os.path.exists(containing_dir):
            os.makedirs(containing_dir)

    # write data to local disk (e.g. while processing an upload)
    # NOTE: updates the record unless save is set to False
    def write_to_disk(self, data, save = True):

        # at various points we may update fields, but we'd like
        # to avoid saving multiple times
        updated_fields = {}

        # we absolutely must have a hash before we can generate
        # a local filename
        if self.hash is None or self.hash == '':
            self.generate_hash()
            updated_fields['hash'] = True

        # make sure we have a generated name (including hash)
        if self.generated_filename is None:
            self.generated_filename = self.generate_filename()
            updated_fields['generated_filename'] = True

        # make sure the directory exists
        # this isn't under exception management because if this
        # fails it reflects a configuration problem that is
        # fairly severe, and it shouldn't be ignored
        self.ensure_local_path_exists()

        # write to local disk
        try:
            with open(self.local_path, 'wb+') as destination:
                if isinstance(data, UploadedFile):
                    # this is a Django uploaded file
                    for chunk in data.chunks():
                        destination.write(chunk)
                else:
                    # we're assuming this is a completed
                    # HTTP request; write it out in chunks
                    chunk_size = 65536
                    while True:
                        chunk = data.read(chunk_size)
                        destination.write(chunk)
                        if len(chunk) < chunk_size:
                            break

        except IOError, e:
            # if we can't write the file, it's corrupted
            # (possibly because of low disk space) so update
            # the metadata record to indicate the file is bad
            self.is_valid = False
            self.remote_status = self.REMOTE_STATUS.LOCAL_CORRUPT
            updated_fields['is_valid'] = True
            updated_fields['remote_status'] = True
            if save:
                self.save(update_fields = updated_fields.keys())
            return

        # update status
        self.remote_status = self.default_remote_status()
        updated_fields['remote_status'] = True
        if save:
            self.save(update_fields = updated_fields.keys())

    # default remote status and expiry times
    @classmethod
    def default_remote_status(cls):
        if settings.SCULPT_S3FILES_REMOTE_MODE == 'local':
            return cls.REMOTE_STATUS.LOCAL_ONLY
        else:
            return cls.REMOTE_STATUS.LOCAL_READY

    @classmethod
    def default_date_expires(cls, now = None):
        if settings.SCULPT_S3FILES_AUTO_EXPIRE_UPLOADS is None:
            return None
        else:
            if now is None:
                now = datetime.datetime.utcnow()
            return now + datetime.timedelta(settings.SCULPT_S3FILES_AUTO_EXPIRE_UPLOADS)

    #
    # derivations
    #

    # fetch a derived image, automatically generating it
    # if it's meant to be lazy-generated
    #
    # NOTE: returns None if no image of that particular
    # derivation exists (or if one would, via lazy
    # generation, but process_lazy has been set to False,
    # or if one exists but it's corrupt)
    #
    # NOTE: this result is cached so repeated queries
    # will not incur repeated database hits, especially
    # from templates using the ParameterProxy.
    #
    # NOTE: if you disable lazy generation, the resulting
    # "no image" response will be cached; use force_reload
    # to bypass the cache and allow lazy generation
    #
    _derivation_cache = None
    def _get_derivation(self, derivation_type, process_lazy = True, force_reload = False):
        derivation = self.DERIVATION_TYPES.get_data_by_id(derivation_type)
        if derivation == None:
            raise Exception('unknown file derivation type')

        # see if we have this derivation
        # check the cache first and return it from there, and
        # populate the cache with what we see in the database
        if self._derivation_cache == None:
            self._derivation_cache = {}
        if derivation_type not in self._derivation_cache or force_reload:
            self._derivation_cache[derivation_type] = self.derivations.filter(derivation_type = derivation_type).first()
            # we'll allow the image to be generated if missing
            allow_lazy = True
        else:
            # we already have a "no" answer in the cache, don't 
            allow_lazy = False
        derived_file = self._derivation_cache[derivation_type]

        if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
            print 'DERIVATION REQUESTED: %s FOUND: %s' % (derivation['id'], unicode(derived_file))

        if derived_file:
            # yes, we have one
            if derived_file.remote_status == self.REMOTE_STATUS.LOCAL_CORRUPT:
                # but it's corrupt; we don't automatically
                # reprocess it, we just act like it's not
                # there (to avoid endlessly redoing the work)
                return None

            # otherwise we'll take this one
            return derived_file

        # at this point, we know we DO NOT have a derivation
        # ready of this type

        # if we're allowed to, generate the missing derivation
        # (this will automatically save the result in the cache)
        if (derivation['mode'] == self.DERIVATION_MODES.LAZY or derivation['mode'] == self.DERIVATION_MODES.IMMEDIATELY) and process_lazy:
            derived_file = self.generate_derivation(derivation_type)
            
        # return whatever we have
        return derived_file
        
    # a version of get_derivation which does not require a
    # parameter directly and is usable in templates
    # NOTE: we give the enumeration as a string rather than
    # as a literal because derived classes will override the
    # enumeration, and we want the parameter proxy to look
    # up the enumeration from the derived class at the time
    # we invoke it, rather than just once here in the base
    # class
    get_derivation = property(parameter_proxy('_get_derivation', 'DERIVATION_TYPES'))

    # actually produce a derived file, given a rule
    # NOTE: if you already have the original image available, pass it
    # in to prevent this function from re-reading it
    def generate_derivation(self, derivation_type, original_image = None):
        derivation = self.DERIVATION_TYPES.get_data_by_id(derivation_type)
        if derivation == None:
            raise Exception('unknown file derivation type')

        if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
            print 'DERIVATION BEING GENERATED: %s' % derivation['id']

        # we're going to write to the cache no matter what, so
        # make sure it's set up
        if self._derivation_cache == None:
            self._derivation_cache = {}

        if self.remote_status == self.REMOTE_STATUS.LOCAL_CORRUPT or not self.is_valid:
            # we already know this file is corrupt; do not
            # attempt to process it
            if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
                if self.remote_status == self.REMOTE_STATUS.LOCAL_CORRUPT:
                    print 'DERIVATION FAILED: corrupted source'
                else:
                    print 'DERIVATION FAILED: file not explicitly marked valid'
            
            # update cache as we know we don't have this derivation type
            self._derivation_cache[derivation_type] = None

            # and give back nothing
            return None

        # if we were not given an original image, we'll need to
        # create one from the disk file
        # NOTE: this won't work if the file isn't local
        # NOTE: we use "is" instead of == because Pillow has a
        # bug, where it attempts to look inside the image, but
        # if the image is invalid, it blows up
        if original_image is None:
            # do the imports here so that we don't depend on Pillow
            # just to include sculpt-s3files
            from PIL import Image

            try:
                original_image = Image.open(self.local_path)
            except IOError, e:
                # any problem with fetching the image will result
                # in no derivation being available--and that result
                # is cached
                if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
                    print 'DERIVATION FAILED: unable to read source; %s' % str(e)

                self._derivation_cache[derivation_type] = None
                return None
                
        # we have an image, run the processing rules to create
        # a new image (see caxiam.s3files.process_images for this
        # code)
        try:
            new_image = process_image(original_image, derivation['operations'])
            
            # we have a new image, construct a new metadata record
            # for it and save it (to generate a hash)
            #
            # We'd rather not save this record until the file is
            # written because if anything goes wrong with that,
            # we have to remove this record, but we need the hash
            # in order to place the file properly, and to generate
            # the hash we need to know the file size, so catch-22.
            # Instead we create the record but mark its status as
            # incomplete so it won't get used.
            #
            now = timezone.now()
            sf = self.__class__.objects.create(
                    original_filename = '_auto_generated.jpg',
                    width = new_image.size[0],          # taken from the image, not the rule, in case some later rule types allow cropped images
                    height = new_image.size[1],
                    mime_type = 'image/jpeg',           # derived images are always JPEG
                    remote_status = self.REMOTE_STATUS.LOCAL_INCOMPLETE,
                    derived_from = self,
                    derivation_type = derivation['value'],
                    date_created = now,
                    date_expires = self.date_expires,   # generated files expire when their parent file expires
                )
                
            # write the image
            # NOTE: if this goes wrong, we have to invalidate it
            try:
                sf.ensure_local_path_exists()
                new_image.save(sf.local_path, quality = 90)
            except IOError, e:
                sf.is_valid = False
                sf.remote_status = self.REMOTE_STATUS.LOCAL_CORRUPT
                sf.save(update_fields = [ 'is_valid', 'remote_status' ])

                if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
                    print 'DERIVATION FAILED: unable to save; %s' % str(e)
                
                # re-raise the exception so we record that we have no
                # derived image
                raise
            
        except Exception, e:
            # any problem with processing the image will result
            # in no derivation being available--and that result
            # is cached
            self._derivation_cache[derivation_type] = None

            # this is how Django logs the exception; see code in
            # django.core.handlers.base
            import logging
            import sys

            logger = logging.getLogger('django.request')
            logger.error('Internal Server Error: %s', 'unknown',
                exc_info=sys.exc_info(),
                extra={
                    'status_code': 500,
                    'request': None
                }
            )

            if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
                print 'DERIVATION FAILED: unable to process; %s' % str(e)
            
            return None

        if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
            print 'DERIVATION COMPLETED: %s' % unicode(sf)

        # otherwise we have a newly-minted derived image, update
        # its status
        sf.remote_status = self.default_remote_status()
        sf.save()

        # save it in the cache and return it
        self._derivation_cache[derivation_type] = sf
        return sf

    # generate all the immediate derivations
    # NOTE: if you already have the original image, you
    # can pass that in so that it won't be re-read from
    # disk
    def generate_immediate_derivations(self, original_image = None):
        if original_image is None:
            from PIL import Image
            # should really catch exceptions from this
            original_image = Image.open(self.local_path)

        if self.is_valid and original_image:
            for derivation in self.file_class.DERIVATION_TYPES.iter_dicts():
                if derivation['mode'] == self.file_class.DERIVATION_MODES.IMMEDIATELY:
                    # this is a process we need to do now, but pass the
                    # original image we have so it won't be re-created
                    self.generate_derivation(derivation['id'], original_image)

    # once an image is confirmed as something to be kept (because
    # the user completed the workflow of whatever the image should
    # be attached to) the expiration time should be cleared
    #
    # as a convenience, if this is called on any derived image,
    # instead we locate the original image; also, all derived
    # images (no matter what we start with) will also be flagged
    # to be kept
    #
    def keep(self):
        keepable = self
        if self.derivation_type is not None:
            keepable = self.derived_from
        keepable.date_expires = None
        keepable.save(update_fields = ['date_expires'])     # saves itself
        keepable.derivations.update(date_expires = None)    # saves its derivations

