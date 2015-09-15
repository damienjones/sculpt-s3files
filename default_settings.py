# you should include these settings before overriding
# them with your project's settings; here is some
# example code:
#
# extra_settings = [
#     'sculpt.s3files.default_settings',
# ]
# import pkgutil
# for es in extra_settings:
#     pkg_loader = pkgutil.get_loader(es)
#     exec open(pkg_loader.filename, 'r') in globals()

# There are three unique base paths for each file:
#
#  1. the local file path; this will be None for files
#     which are only stored remotely
#
#  2. the "internal" URL, which is strictly for the
#     application to communicate with its host (e.g.
#     nginx internal redirect for protected-access
#     files)
#
#  3. the "external" URL, which is the one given to
#     the end user; for protected files, this routes
#     through a view which controls access
#
# For unprotected files, 2 and 3 will be the same and
# will generally be served directly from the web server
# rather than mediated by application code.

# high-level upload settings
SCULPT_S3FILES_AUTO_EXPIRE_UPLOADS = 1.0    # default time, in days, before uploads auto-expire; use None to disable
SCULPT_S3FILES_CHECK_IMAGES = True          # whether to extract image metadata at upload time

# where to store/serve uploaded files
SCULPT_S3FILES_REMOTE_MODE = 'local'        # one of 'local', 's3'
SCULPT_S3FILES_LOCAL_DIR = MEDIA_ROOT       # base directory of local files
SCULPT_S3FILES_BUCKET = None                # S3 bucket name
SCULPT_S3FILES_BUCKET_DIR = None            # path within the S3 bucket
SCULPT_S3FILES_SPLIT_CHARS = 1              # how many characters of the hash to use when building paths
SCULPT_S3FILES_SPLIT_LEVELS = 2             # how many levels of splitting to do
SCULPT_S3FILES_INTERNAL_URL = MEDIA_URL     # internal URL base path for media files
SCULPT_S3FILES_EXTERNAL_URL = MEDIA_URL     # external URL base path for media files
SCULPT_S3FILES_SERVER_TYPE = 'nginx'        # 'nginx' or 'apache', controls how internal redirects are done

# debug settings
SCULPT_S3FILES_DUMP_RESPONSES = False       # whether to emit debug data about files served
SCULPT_S3FILES_DUMP_DERIVATIONS = False     # whether to emit debug data about image derivations
