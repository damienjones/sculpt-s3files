from django.conf import settings
from sculpt.common import Enumeration

# tools for processing images

# how to describe a transformation
RESIZE_MODES = Enumeration(
        (0, 'CROP'),            # existing image must fill the requested size, with excess cropped
        (1, 'EXPAND'),          # existing image must all appear within the requested size, with padding
        (2, 'MINIMUM_SIZE'),    # do not pad or crop, but use this minimum size
        (3, 'MAXIMUM_SIZE'),    # do not pad or crop, but use this maximum size
    )
ANCHOR_HORIZONTAL = Enumeration(
        (0, 'LEFT'),
        (1, 'CENTER'),
        (2, 'RIGHT'),
    )
ANCHOR_VERTICAL = Enumeration(
        (0, 'TOP'),
        (1, 'CENTER'),
        (2, 'BOTTOM'),
    )

# actually process the image; returns new thumbnail
# (which must be saved separately)
#
# NOTE: this is non-destructive on source_image so that
# multiple derived images can be made without reloading
# the source
#
def process_image(source_image, operations):
    # do the imports here so that we don't depend on Pillow just to
    # include sculpt-s3files
    from PIL import Image, ImageFile

    # each process is a sequence of operations
    working_image = source_image
    for op in operations:
        if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
            print '  DERIVATION OPERATION: %s' % repr(op)

        if op['operation'] == 'resize':
            # a resize operation is a combination of resizing, cropping,
            # and padding

            # before we resize, we want to prepare a version of the
            # working image that is the same aspect ratio as the target
            # size; we need to determine whether to crop or pad and to
            # which axis those need to be done
            #
            current_aspect_ratio = float(working_image.size[0]) / float(working_image.size[1])
            target_aspect_ratio = float(op['target_size'][0]) / float(op['target_size'][1])
            is_too_wide = current_aspect_ratio > target_aspect_ratio
            force_rgb = True
            
            if op['resize_mode'] == RESIZE_MODES.MINIMUM_SIZE:
                pass    # TODO

            elif op['resize_mode'] == RESIZE_MODES.MAXIMUM_SIZE:
                pass    # TODO

            else:
                # pad or crop
                if (op['resize_mode'] == RESIZE_MODES.CROP and is_too_wide) or (op['resize_mode'] == RESIZE_MODES.EXPAND and not is_too_wide):
                    # determine the size by the height
                    next_size = ( int(working_image.size[1] * target_aspect_ratio), working_image.size[1] )
                else:
                    # determine the size by the width
                    next_size = ( working_image.size[0], int(working_image.size[0] / target_aspect_ratio) )

            # determine the anchor point for the crop
            if op['anchor_horizontal'] == ANCHOR_HORIZONTAL.LEFT:
                x = 0
            elif op['anchor_horizontal'] == ANCHOR_HORIZONTAL.CENTER:
                x = int((working_image.size[0] - next_size[0]) / 2)
            elif op['anchor_horizontal'] == ANCHOR_HORIZONTAL.RIGHT:
                x = working_image.size[0] - next_size[0]

            if op['anchor_vertical'] == ANCHOR_VERTICAL.TOP:
                y = 0
            elif op['anchor_vertical'] == ANCHOR_VERTICAL.CENTER:
                y = int((working_image.size[1] - next_size[1]) / 2)
            elif op['anchor_vertical'] == ANCHOR_VERTICAL.BOTTOM:
                y = working_image.size[1] - next_size[1]

            # sometimes we get an image that is a paletted image rather
            # than RGB; if we've flagged this operation to force it to
            # RGB (which is required if we're going to resize and not
            # have it look bad) take care of that
            if force_rgb and working_image.mode != 'RGB':
                working_image = working_image.convert(mode = 'RGB')

            if op['resize_mode'] == RESIZE_MODES.CROP:
                # perform the crop, producing a copy because we do not
                # want to modify the source image
                crop_box = ( x, y, x+next_size[0], y+next_size[1] )
                next_image = working_image.crop(crop_box)

                if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
                    print '    cropping %s from %s to %s' % (repr(working_image.size), repr(crop_box), repr(next_image.size))

            else:
                # perform the expand
                # Pillow doesn't have a native method for doing this that
                # allows us to expand different amounts on each axis so
                # we do it the hard way
                # NOTE: because of the way our anchors are calculated, the
                # offsets x,y will be negative when we are expanding rather
                # than cropping
                paste_box = ( -x, -y, -x+working_image.size[0], -y+working_image.size[1] )
                next_image = Image.new('RGB', next_size, op['background_color'])
                next_image.paste(working_image, paste_box)

                if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
                    print '    expanding %s at %s to %s' % (repr(working_image.size), repr(crop_box), repr(next_image.size))

            # shrink the cropped/expanded image
            next_image.thumbnail(op['target_size'], Image.ANTIALIAS)

            if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
                print '    resizing to %s' % repr(next_image.size)

        # transition to image generated at this step; if the
        # previous working image isn't the starting image,
        # discard it to keep memory consumption down
        if working_image is not source_image:
            del working_image
            if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
                print '    deleting intermediate image'
        working_image = next_image

    # done
    if settings.SCULPT_S3FILES_DUMP_DERIVATIONS:
        print '  DERIVATION OPERATIONS COMPLETE'
    return working_image
