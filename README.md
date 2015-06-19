sculpt-s3files
==============

Django file uploads are a disgrace.

In today's internet we should never build a site that fundamentally breaks if more than one web server is used. Yet so many examples of how to process file uploads behave this way; they assume that the file, as uploaded onto the web server, can simply be moved to a different directory and be served to end users directly. This doesn't work at all in a load-balanced site.

One option is to store such files in an S3 bucket and have users directly upload there, giving them a unique upload URL/credentials every time they wish to upload a file. This works exceptionally well, _if_ no processing needs to be done on the uploaded file. Since this module offers exactly that kind of processing, it takes a different approach: upload the file to the web server, process it as required, and _then_ migrate it to S3.

Special Note
------------

This is not a complete project. It's not packaged as a Python project (so you have to drop the source into your own project directly), the directory structure doesn't match the package's expectations of itself, there are no unit tests, and the only documentation is within the code itself. I don't really expect anyone else to use this code... yet. All of those things will be addressed at some point.

That said, the code _is_ being used. This started with work I did while at Caxiam (and I obtained a comprehensive license to continue with the code) so here and there are references to Caxiam that I am slowly replacing. I've done quite a bit of refactoring since then and expect to do more.

