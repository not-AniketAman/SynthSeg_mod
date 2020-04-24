# python imports
import keras
import numpy as np
import tensorflow as tf
import keras.layers as KL
import keras.backend as K

# third-party imports
from ext.lab2im import utils
from ext.lab2im import sample_gmm as l2i_gmm
from ext.lab2im import edit_tensors as l2i_et
from ext.lab2im import spatial_augmentation as l2i_sp
from ext.lab2im import intensity_augmentation as l2i_ia


def labels_to_image_model(labels_shape,
                          n_channels,
                          generation_labels,
                          output_labels,
                          n_neutral_labels,
                          atlas_res,
                          target_res,
                          output_shape=None,
                          output_div_by_n=None,
                          padding_margin=None,
                          flipping=True,
                          aff=None,
                          apply_linear_trans=True,
                          apply_nonlin_trans=True,
                          nonlin_std=3.,
                          nonlin_shape_factor=.0625,
                          blur_background=True,
                          data_res=None,
                          thickness=None,
                          downsample=False,
                          blur_range=1.15,
                          crop_channel2=None,
                          apply_bias_field=True,
                          bias_field_std=.3,
                          bias_shape_factor=.025):
    """
    This function builds a keras/tensorflow model to generate images from provided label maps.
    The images are generated by sampling a Gaussian Mixture Model (of given parameters), conditionned on the label map.
    The model will take as inputs:
        -a label map
        -a vector containing the means of the Gaussian Mixture Model for each label,
        -a vector containing the standard deviations of the Gaussian Mixture Model for each label,
        -if apply_affine_deformation is True: a batch*(n_dims+1)*(n_dims+1) affine matrix
        -if apply_non_linear_deformation is True: a small non linear field of size batch*(dim_1*...*dim_n)*n_dims that
        will be resampled to labels size and integrated, to obtain a diffeomorphic elastic deformation.
        -if apply_bias_field is True: a small bias field of size batch*(dim_1*...*dim_n)*1 that will be resampled to
        labels size and multiplied to the image, to add a "bias-field" noise.
    The model returns:
        -the generated image normalised between 0 and 1.
        -the corresponding label map, with only the labels present in output_labels (the other are reset to zero).
    :param labels_shape: shape of the input label maps. Can be a sequence or a 1d numpy array.
    :param n_channels: number of channels to be synthetised.
    :param generation_labels: (optional) list of all possible label values in the input label maps.
    Default is None, where the label values are directly gotten from the provided label maps.
    If not None, can be a sequence or a 1d numpy array. It should be organised as follows: background label first, then
    non-sided labels (e.g. CSF, brainstem, etc.), then all the structures of the same hemisphere (can be left or right),
    and finally all the corresponding contralateral structures (in the same order).
    :param output_labels: list of all the label values to keep in the output label maps, in no particular order.
    Should be a subset of the values contained in generation_labels.
    Label values that are in generation_labels but not in output_labels are reset to zero.
    Can be a sequence or a 1d numpy array.
    :param n_neutral_labels: number of non-sided generation labels.
    :param atlas_res: resolution of the input label maps.
    Can be a number (isotropic resolution), a sequence, or a 1d numpy array.
    :param target_res: target resolution of the generated images and corresponding label maps.
    Can be a number (isotropic resolution), a sequence, or a 1d numpy array.
    :param output_shape: (optional) desired shape of the output images, obtained by cropping.
    Can be an integer (same size in all dimensions), a sequence, a 1d numpy array, or the path to a 1d numpy array.
    :param output_div_by_n: (optional) forces the output shape to be divisible by this value. It overwrites output_shape
    if necessary. Can be an integer (same size in all dimensions), a sequence, or a 1d numpy array.
    :param padding_margin: (optional) margin by which to pad the input labels with zeros.
    Padding is applied prior to any other operation.
    Can be an integer (same padding in all dimensions), a sequence, or a 1d numpy array. Default is no padding.
    :param flipping: (optional) whether to introduce right/left random flipping
    :param aff: (optional) example of an (n_dims+1)x(n_dims+1) affine matrix of one of the input label map.
    Used to find brain's right/left axis. Should be given if flipping is True.
    :param apply_linear_trans: (optional) whether to linearly deform the input label maps prior to generation.
    If true, the model will take an additional input of size batch*(n_dims+1)*(n_dims+1). Default is True.
    :param apply_nonlin_trans: (optional) whether to non-linearly deform the input label maps prior to generation.
    If true, the model will take an additional input of size batch*(dim_1*...*dim_n)*n_dims. Default is True.
    :param nonlin_std: (optional) If apply_nonlin_trans is True, standard deviation of the normal distribution
    from which we sample the first tensor for synthesising the deformation field.
    :param nonlin_shape_factor: (optional) if apply_non_linear_deformation is True, factor between the shapes of the
    input label maps and the shape of the input non-linear tensor.
    :param blur_background: (optional) If True, the background is blurred with the other labels, and can be reset to
    zero with a probability of 0.2. If False, the background is not blurred (we apply an edge blurring correction), and
    can be replaced by a low-intensity background.
    :param data_res: ((optional) acquisition resolution to mimick. If provided, the images sampled from the GMM are
    blurred to mimick data that would be: 1) acquired at the given acquisition resolution, and 2) resample at
    target_resolution.
    Default is None, where images are isotropically blurred to introduce some spatial correlation between voxels.
    If the generated images are uni-modal, data_res can be a number (isotropic acquisition resolution), a sequence, a 1d
    numpy array, or the path to a 1d numy array. In the multi-modal case, it should be given as a numpy array (or a
    path) of size (n_mod, n_dims), where each row is the acquisition resolution of the correspionding chanel.
    :param thickness: (optional) if data_res is provided, we can further specify the slice thickness of the low
    resolution images to mimick.
    If the generated images are uni-modal, data_res can be a number (isotropic acquisition resolution), a sequence, a 1d
    numpy array, or the path to a 1d numy array. In the multi-modal case, it should be given as a numpy array (or a
    path) of size (n_mod, n_dims), where each row is the acquisition resolution of the correspionding chanel.
    :param downsample: (optional) whether to actually downsample the volume image to data_res. Default is False.
    :param blur_range: (optional) Randomise the standard deviation of the blurring kernels, (whether data_res is given
    or not). At each mini_batch, the standard deviation of the blurring kernels are multiplied by a coefficient sampled
    from a uniform distribution with bounds [1/blur_range, blur_range]. If None, no randomisation. Default is 1.15.
    :param crop_channel2: (optional) stats for cropping second channel along the anterior-posterior axis.
    Should be a vector of length 4, with bounds of uniform distribution for cropping the front and back of the image
    (in percentage). None is no croppping.
    :param apply_bias_field: (optional) whether to apply a bias field to the generated image.
    If true, the model will take an additional input of size batch*(dim_1*...*dim_n)*1. Default is True.
    :param bias_field_std: (optional) If apply_nonlin_trans is True, standard deviation of the normal
    distribution from which we sample the first tensor for synthesising the deformation field.
    :param bias_shape_factor: (optional) if apply_bias_field is True, factor between the shapes of the
    input label maps and the shape of the input bias field tensor.
    """

    # reformat resolutions
    labels_shape = utils.reformat_to_list(labels_shape)
    n_dims, _ = utils.get_dims(labels_shape)
    atlas_res = utils.reformat_to_n_channels_array(atlas_res, n_dims=n_dims, n_channels=n_channels)
    if data_res is None:  # data_res assumed to be the same as the atlas
        data_res = atlas_res
    else:
        data_res = utils.reformat_to_n_channels_array(data_res, n_dims=n_dims, n_channels=n_channels)
    atlas_res = atlas_res[0]
    if downsample:  # same as data_res if we want to actually downsample the synthetic image
        downsample_res = data_res
    else:  # set downsample_res to None if downsampling is not necessary
        downsample_res = None
    if target_res is None:
        target_res = atlas_res
    else:
        target_res = utils.reformat_to_n_channels_array(target_res, n_dims)[0]
    thickness = utils.reformat_to_n_channels_array(thickness, n_dims=n_dims, n_channels=n_channels)

    # get shapes
    crop_shape, output_shape, padding_margin = get_shapes(labels_shape, output_shape, atlas_res, target_res,
                                                          padding_margin, output_div_by_n)

    # create new_label_list and corresponding LUT to make sure that labels go from 0 to N-1
    n_generation_labels = generation_labels.shape[0]
    new_generation_label_list, lut = utils.rearrange_label_list(generation_labels)

    # define model inputs
    labels_input = KL.Input(shape=labels_shape+[1], name='labels_input')
    means_input = KL.Input(shape=list(new_generation_label_list.shape) + [n_channels], name='means_input')
    std_devs_input = KL.Input(shape=list(new_generation_label_list.shape) + [n_channels], name='std_devs_input')
    list_inputs = [labels_input, means_input, std_devs_input]
    if apply_linear_trans:
        aff_in = KL.Input(shape=(n_dims + 1, n_dims + 1), name='aff_input')
        list_inputs.append(aff_in)
    else:
        aff_in = None

    # convert labels to new_label_list
    labels = l2i_et.convert_labels(labels_input, lut)

    # pad labels
    if padding_margin is not None:
        pad = np.transpose(np.array([[0] + padding_margin + [0]] * 2))
        labels = KL.Lambda(lambda x: tf.pad(x, tf.cast(tf.convert_to_tensor(pad), dtype='int32')), name='pad')(labels)
        labels_shape = labels.get_shape().as_list()[1:n_dims+1]

    # deform labels
    if apply_linear_trans | apply_nonlin_trans:
        labels = l2i_sp.deform_tensor(labels, aff_in, apply_nonlin_trans, 'nearest', nonlin_std, nonlin_shape_factor)
    labels = KL.Lambda(lambda x: tf.cast(x, dtype='int32'))(labels)

    # cropping
    if crop_shape != labels_shape:
        labels, _ = l2i_sp.random_cropping(labels, crop_shape, n_dims)

    if flipping:
        assert aff is not None, 'aff should not be None if flipping is True'
        labels, _ = l2i_sp.label_map_random_flipping(labels, new_generation_label_list, n_neutral_labels, aff, n_dims)

    # build synthetic image
    image = l2i_gmm.sample_gmm_conditioned_on_labels(labels, means_input, std_devs_input, n_generation_labels,
                                                     n_channels)

    # loop over channels
    if n_channels > 1:
        split = KL.Lambda(lambda x: tf.split(x, [1] * n_channels, axis=-1))(image)
    else:
        split = [image]
    mask = KL.Lambda(lambda x: tf.where(tf.greater(x, 0), tf.ones_like(x, dtype='float32'),
                                        tf.zeros_like(x, dtype='float32')))(labels)
    processed_channels = list()
    for i, channel in enumerate(split):

        # reset edges of second channels to zero
        if (crop_channel2 is not None) & (i == 1):  # randomly crop sides of second channel
            channel, tmp_mask = l2i_sp.restrict_tensor(channel, axes=3, boundaries=crop_channel2)
        else:
            tmp_mask = None

        # blur channel
        if thickness is not None:
            sigma = utils.get_std_blurring_mask_for_downsampling(data_res[i], atlas_res, thickness=thickness[i])
        else:
            sigma = utils.get_std_blurring_mask_for_downsampling(data_res[i], atlas_res)
        kernels_list = l2i_et.get_gaussian_1d_kernels(sigma, blurring_range=blur_range)
        channel = l2i_et.blur_channel(channel, mask, kernels_list, n_dims, blur_background)
        if (crop_channel2 is not None) & (i == 1):
            channel = KL.multiply([channel, tmp_mask])

        # resample channel
        if downsample_res is not None:
            channel = l2i_et.resample_tensor(channel, output_shape, 'linear', downsample_res[i], atlas_res,
                                             n_dims=n_dims)
        else:
            channel = l2i_et.resample_tensor(channel, output_shape, 'linear', None, atlas_res, n_dims)

        # apply bias field
        if apply_bias_field:
            channel = l2i_ia.bias_field_augmentation(channel, bias_field_std, bias_shape_factor)

        # intensity augmentation
        channel = KL.Lambda(lambda x: K.clip(x, 0, 300))(channel)
        channel = l2i_ia.min_max_normalisation(channel)
        processed_channels.append(l2i_ia.gamma_augmentation(channel, std=0.5))

    # concatenate all channels back
    if n_channels > 1:
        image = KL.concatenate(processed_channels)
    else:
        image = processed_channels[0]

    # resample labels at target resolution
    if crop_shape != output_shape:
        labels = KL.Lambda(lambda x: tf.cast(x, dtype='float32'))(labels)
        labels = l2i_et.resample_tensor(labels, output_shape, interp_method='nearest', n_dims=3)
    # convert labels back to original values and reset unwanted labels to zero
    labels = l2i_et.convert_labels(labels, generation_labels)
    labels_to_reset = [lab for lab in generation_labels if lab not in output_labels]
    labels = l2i_et.reset_label_values_to_zero(labels, labels_to_reset)
    labels = KL.Lambda(lambda x: tf.cast(x, dtype='int32'), name='labels_out')(labels)

    # build model (dummy layer enables to keep the labels when plugging this model to other models)
    image = KL.Lambda(lambda x: x[0], name='image_out')([image, labels])
    brain_model = keras.Model(inputs=list_inputs, outputs=[image, labels])

    return brain_model


def get_shapes(labels_shape, output_shape, atlas_res, target_res, padding_margin, output_div_by_n):

    n_dims = len(atlas_res)

    # get new labels shape if padding
    if padding_margin is not None:
        padding_margin = utils.reformat_to_list(padding_margin, length=n_dims, dtype='int')
        labels_shape = [labels_shape[i] + 2 * padding_margin[i] for i in range(n_dims)]

    # get resampling factor
    if atlas_res.tolist() != target_res.tolist():
        resample_factor = [atlas_res[i] / float(target_res[i]) for i in range(n_dims)]
    else:
        resample_factor = None

    # output shape specified, need to get cropping shape, and resample shape if necessary
    if output_shape is not None:
        output_shape = utils.reformat_to_list(output_shape, length=n_dims, dtype='int')

        # make sure that output shape is smaller or equal to label shape
        if resample_factor is not None:
            output_shape = [min(int(labels_shape[i] * resample_factor[i]), output_shape[i]) for i in range(n_dims)]
        else:
            output_shape = [min(labels_shape[i], output_shape[i]) for i in range(n_dims)]

        # make sure output shape is divisible by output_div_by_n
        if output_div_by_n is not None:
            tmp_shape = [utils.find_closest_number_divisible_by_m(s, output_div_by_n, smaller_ans=True)
                         for s in output_shape]
            if output_shape != tmp_shape:
                print('output shape {0} not divisible by {1}, changed to {2}'.format(output_shape, output_div_by_n,
                                                                                     tmp_shape))
                output_shape = tmp_shape

        # get cropping and resample shape
        if resample_factor is not None:
            cropping_shape = [int(np.around(output_shape[i]/resample_factor[i], 0)) for i in range(n_dims)]
        else:
            cropping_shape = output_shape

    # no output shape specified, so no cropping unless label_shape is not divisible by output_div_by_n
    else:
        cropping_shape = labels_shape
        if resample_factor is not None:
            output_shape = [int(np.around(cropping_shape[i]*resample_factor[i], 0)) for i in range(n_dims)]
        else:
            output_shape = cropping_shape
        # make sure output shape is divisible by output_div_by_n
        if output_div_by_n is not None:
            output_shape = [utils.find_closest_number_divisible_by_m(s, output_div_by_n, smaller_ans=False)
                            for s in output_shape]

    return cropping_shape, output_shape, padding_margin
