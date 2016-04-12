from django import forms
from django.conf import settings
from django.utils import timezone
from sculpt.ajax.forms import AjaxForm, AjaxUploadFormMixin
from sculpt.ajax.responses import AjaxDataResponse
from sculpt.ajax.views import AjaxFormView
from crispy_forms.layout import Layout, Row, Div, Submit, HTML
import datetime
from PIL import Image

# a single-file upload form; when the first
# file is uploaded, the upload field gets hidden
class SingleFileUploadForm(AjaxUploadFormMixin, AjaxForm):
    
    def setup_target_field(self, target_queue_id, target_field_id):
        self.helper.attrs['data-queue-id'] = target_queue_id
        self.helper.attrs['data-target-field-id'] = target_field_id
    
    def setup_form_helper(self, helper):
        helper.attrs = {
                'data-max-files': '1',
            }
        helper.layout = Layout(
                'uploaded_file',
                HTML('{% include "sculpt_s3files/upload_single.html" %}'),
            )

        return super(SingleFileUploadForm, self).setup_form_helper(helper)


# by default, we just accept the uploaded form, create
# a storage record for it, and move the file to the
# appropriate place; extend this class to add more
# restrictions, such as per-user quotas
#
class AjaxFileUploadView(AjaxFormView):
    form_class = SingleFileUploadForm   # by default
    file_class = None                   # must be an app-specific AbstractStoredFile-derived class

    # assuming we're not just uploading for fun,
    # the host page script for managing uploads
    # will want to know what form/field should
    # be modified to have the file ID
    target_queue_id = ''
    target_field_id = ''

    # we might have derivations that would be useful
    # to return along with the uploaded file, if
    # they are supported; list those here
    #
    # NOTE: they will only be included if they are
    # already in the derivation cache, which will
    # only happen if that particular derivation is
    # both supported by the uploaded file type and
    # is flagged to be generated immediately
    #
    # NOTE: you should include THUMBNAIL if at all
    # possible as the client-side AJAX handler will
    # look for this
    #
    include_derivations = [ 'THUMBNAIL' ]

    def prepare_form(self, form, form_alias):
        form.setup_target_field(self.target_queue_id, self.target_field_id)

    def process_form(self, form, form_alias):
        # the form is valid and the file is either in RAM or
        # stored in a temporary disk file, but either way,
        # we are going to store it

        # quick reference to uploaded file object
        # NOTE: make sure to account for prefix
        uf = self.request.FILES[form.add_prefix('uploaded_file')]
        
        # before we can store it in a permanent place we need
        # a guaranteed-unique filename for it, so we need to
        # create and save the StoredFile record
        sf_attributes = self.get_stored_file_attributes(form)
        sf = self.file_class(**sf_attributes)

        # before we save the record, see if it's an image type
        # that Pillow understands; if so, extract the metadata
        original_image = None
        if settings.SCULPT_S3FILES_CHECK_IMAGES and sf.is_image:
            try:
                if not hasattr(uf, 'temporary_file_path'):
                    # entire file is in memory already
                    original_image = Image.open(uf)
                    
                else:
                    # file is multiple chunks, so written to
                    # disk
                    original_image = Image.open(uf.temporary_file_path())

                # valid image, extract metadata            
                sf.width = original_image.size[0]
                sf.height = original_image.size[1]
                sf.is_valid = True
            
            except IOError, e:
                # the file seems to be invalid
                sf.is_valid = False
            
        # write or move the file
        # NOTE: if anything goes wrong, the stored file
        # will be marked as invalid
        sf.write_to_disk(uf, save = False)
        
        # we're done parsing the file; save the meta data
        # record to generate its hash and local file path
        sf.save()
        
        # see if we need to create any derived images
        if sf.is_valid and original_image:
            for derivation in self.file_class.DERIVATION_TYPES.iter_dicts():
                if derivation['mode'] == self.file_class.DERIVATION_MODES.IMMEDIATELY:
                    # this is a process we need to do now, but pass the
                    # original image we have so it won't be re-created
                    sf.generate_derivation(derivation['id'], original_image)

        # we save this in the local object so that if we need
        # to extend this class we can get at the results
        # without having to hack it up
        self.stored_file = sf
        
        # return a default result set
        results = self.generate_results(sf)
        return self.prepare_results(form, form_alias, sf, results)

    # in case you need to override the results returned
    def prepare_results(self, form, form_alias, sf, results):
        return AjaxDataResponse(results)

    # invoked when the form is processed; override this to
    # customize the creation of the StoredFile-derived
    # metadata record, such as if it requires links to its
    # owner or other records
    #
    # NOTE: the record isn't actually created here, just the
    # parameters used to create it
    #
    def get_stored_file_attributes(self, form):
        now = timezone.now()
        uf = self.request.FILES[form.add_prefix('uploaded_file')]   # make sure to include prefix
        return {
                'original_filename': uf.name,
                'size': uf.size,
                'mime_type': uf.content_type,
                # remote_status should rely on the default in the model class
                #'remote_status': self.file_class.REMOTE_STATUS.LOCAL_INCOMPLETE,
                'date_created': now,
                'date_expires': self.file_class.default_date_expires(now),
            }

    # generate result data, given a stored file record;
    # override this if you need to customize results
    def generate_results(self, sf):
        results = {
                'file': {
                        'hash': sf.hash,
                        'size': sf.size,
                        'url': sf.external_url,     # always the external one
                        'width': sf.width,
                        'height': sf.height,
                        'is_image': sf.is_image,
                        'is_video': sf.is_video,
                        'is_audio': sf.is_audio,
                    }
            }

        # add in any requested derivations, if
        # they are available
        if sf._derivation_cache is not None:
            for derivation_id in self.include_derivations:
                if derivation_id in sf._derivation_cache:
                    dsf = sf._get_derivation(derivation_id)   # _get_derivation to bypass ParameterProxy
                    results[derivation_id.lower()] = {
                            'hash': dsf.hash,
                            'size': dsf.size,
                            'url': dsf.external_url,    # always the external one
                            'width': dsf.width,
                            'height': dsf.height,
                            'is_image': dsf.is_image,
                            'is_video': dsf.is_video,
                            'is_audio': dsf.is_audio,
                        }

        return results
