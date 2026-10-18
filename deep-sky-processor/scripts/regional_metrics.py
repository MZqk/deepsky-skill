"""Registered regional diagnostics; normalized high values are not sensor saturation."""

import numpy as np
from scipy.ndimage import gaussian_filter
from artifact_gates import check_core_plateau, check_channel_clipping

M42_REGIONS = {
    'core': {'center_xy': [1008, 1704], 'radius': 120},
    'inner': {'center_xy': [1008, 1704], 'inner_radius': 120, 'radius': 360},
    'outer': {'center_xy': [1008, 1704], 'inner_radius': 360, 'radius': 640},
    'background': {'bbox_xywh': [64, 64, 512, 512]},
}


def region_masks(shape, definitions=M42_REGIONS):
    h, w = shape[:2]
    yy, xx = np.ogrid[:h, :w]
    masks = {}
    for name, spec in definitions.items():
        if 'bbox_xywh' in spec:
            x, y, width, height = spec['bbox_xywh']
            mask = (xx >= x) & (xx < x + width) & (yy >= y) & (yy < y + height)
        else:
            cx, cy = spec['center_xy']
            radius = spec['radius']
            inner = spec.get('inner_radius', 0)
            if not 0 <= inner < radius:
                raise ValueError('Invalid regional radius')
            distance = (xx - cx) ** 2 + (yy - cy) ** 2
            mask = (distance <= radius ** 2) & (distance > inner ** 2 if inner else True)
        if not mask.any():
            raise ValueError(f'Region {name} is outside the image')
        masks[name] = mask
    return masks


def regional_metrics(image, reference, masks, star_mask=None, processing_stage='final'):
    image, reference = np.asarray(image, dtype=np.float32), np.asarray(reference, dtype=np.float32)
    if image.shape != reference.shape or not np.isfinite(image).all() or not np.isfinite(reference).all():
        raise ValueError('Regional diagnostics require finite registered geometry')
    if any(np.shape(mask) != image.shape[:2] for mask in masks.values()):
        raise ValueError('Regional mask geometry must match')
    if star_mask is not None and np.shape(star_mask) != image.shape[:2]:
        raise ValueError('Regional star mask geometry must match')
    gray = image.mean(axis=2) if image.ndim == 3 else image
    result = {'processing_stage': processing_stage, 'coordinate_convention': 'zero_based_xy', 'luminance_definition': 'mean_rgb',
              'regions': {}}
    for name, roi in masks.items():
        mask = roi & ~(np.asarray(star_mask) > .5) if star_mask is not None and name in ('outer', 'background') else roi
        if np.count_nonzero(mask) < 25:
            raise ValueError(f'Region {name} has insufficient unmasked samples')
        values = gray[mask]
        p10, median, p90, p99 = np.percentile(values, [10, 50, 90, 99])
        rows, cols = np.where(roi)
        bounds = (slice(rows.min(), rows.max()+1), slice(cols.min(), cols.max()+1))
        cropped = image[bounds]
        source = reference[bounds]
        crop_roi = mask[bounds]
        crop_gray = gray[bounds]
        plateau = check_core_plateau(cropped, source, subject_mask=crop_roi,
                                      minimum_level=float(p90), region_mask=crop_roi)
        channels = {}
        for i, channel in enumerate(('r', 'g', 'b') if image.ndim == 3 else ('mono',)):
            band = image[..., i] if image.ndim == 3 else image
            original_band = source[..., i] if image.ndim == 3 else source
            samples = band[mask]
            threshold = float(np.percentile(samples, 90))
            channel_plateau = check_core_plateau(band[bounds], original_band,
                                                 subject_mask=crop_roi, minimum_level=threshold,
                                                 region_mask=crop_roi)
            channels[channel] = {'p90': threshold, 'p99': float(np.percentile(samples, 99)),
                                 'max': float(samples.max()),
                                 'high_value_ratio': float(np.mean(samples >= .95)),
                                 'clip_ratio': float(np.mean(samples >= .995)),
                                 'plateau': channel_plateau['value']}
        smooth = gaussian_filter(crop_gray, sigma=2)
        gy, gx = np.gradient(smooth)
        contrast = float(p90-p10)
        weighted = np.where(crop_roi, crop_gray, 0)
        profiles = {}
        for axis, label, origin in ((0, 'x', cols.min()), (1, 'y', rows.min())):
            counts = crop_roi.sum(axis=axis)
            means = np.divide(weighted.sum(axis=axis), counts,
                              out=np.zeros_like(counts, dtype=float), where=counts > 0)
            indices = np.arange(0, len(counts), max(1, len(counts)//64))
            profiles['profile_' + label] = [float(means[i]) if counts[i] else None for i in indices]
            profiles['profile_' + label + '_positions'] = (indices + origin).astype(int).tolist()
        result['regions'][name] = {
            'bbox_xywh': [int(cols.min()), int(rows.min()), int(crop_gray.shape[1]), int(crop_gray.shape[0])],
            'plateau_coordinate_frame': 'region_bbox_row_col',
            'sample_count': int(mask.sum()), 'p10': float(p10), 'median': float(median),
            'p90': float(p90), 'p99': float(p99), 'contrast_span': contrast,
            'relative_contrast': contrast / max(abs(float(median)), 1e-8),
            'mean_gradient_sigma2': float(np.mean(np.hypot(gx, gy)[crop_roi])),
            'channels': channels, 'plateau': plateau,
            **profiles,
        }
    result['channel_clipping'] = check_channel_clipping(image) if processing_stage != 'linear' else None
    background = result['regions']['background']['median']
    for values in result['regions'].values():
        values['background_subtracted_median'] = values['median'] - background
    return result


def saturation_evidence(meta):
    header = meta.get('header') or {}
    threshold = header.get('SATURATE', header.get('SATLEVEL'))
    return {'original_min': meta.get('original_min'), 'original_max': meta.get('original_max'),
            'normalization_scale': meta.get('data_scale'), 'normalization_offset': meta.get('data_offset'),
            'bscale': meta.get('bscale'), 'bzero': meta.get('bzero'), 'bitpix': header.get('BITPIX'),
            'stack_count': header.get('STACKCNT'), 'declared_saturation_threshold': threshold,
            'sensor_saturation': 'suspected' if threshold is None else 'metadata_threshold_available',
            'evidence': 'Normalized maxima and stacked plateaus do not prove sensor saturation'}


def native_ring_profiles(images, center_xy, fwhm, star_mask=None):
    """Measure each channel in one native geometry without cross-curve subtraction."""
    from artifact_gates import _local_background, RING_DIP_REL
    cx, cy = map(int, center_xy)
    radius = max(64, int(np.ceil(5*fwhm + 2)))
    shape = next(iter(images.values())).shape
    x0, x1 = max(0, cx-radius), min(shape[1], cx+radius+1)
    y0, y1 = max(0, cy-radius), min(shape[0], cy+radius+1)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    dx, dy = xx-cx, yy-cy
    distance = np.hypot(dx, dy)
    outer = (distance >= 3.5*fwhm) & (distance <= 5*fwhm)
    valid = np.ones_like(outer) if star_mask is None else star_mask[y0:y1, x0:x1] <= .5
    outer &= valid
    edges = np.arange(0, 5*fwhm+1, max(1., .3*fwhm))
    report = {'center_xy': [cx, cy], 'fwhm_px': float(fwhm),
              'bbox_xywh': [x0, y0, x1-x0, y1-y0], 'profiles': {},
              'attribution': 'unconfirmed',
              'note': 'Separate per-channel local planes; different curves are not subtracted.'}
    for name, image in images.items():
        if image.shape != shape:
            raise ValueError('Native ring evidence requires registered image geometry')
        channels = {}
        for channel, index in (('r',0),('g',1),('b',2)):
            patch = image[y0:y1, x0:x1,index]
            fitted = _local_background(patch, dx, dy, outer)
            if fitted is None:
                channels[channel] = {'status': 'insufficient_background'}
                continue
            plane, error = fitted
            background = float(np.median(plane[outer]))
            bins = []
            for lo, hi in zip(edges[:-1], edges[1:]):
                selected = (distance >= lo) & (distance < hi) & (valid | (distance <= 1.2*fwhm+1))
                if selected.sum() < 3:
                    continue
                residual = float(np.mean((patch-plane)[selected]))
                bins.append({'radius_px': float((lo+hi)/2), 'residual': residual,
                             'relative_residual': residual/max(abs(background),1e-12)})
            minimum = min((item['relative_residual'] for item in bins
                           if 1.2*fwhm <= item['radius_px'] <= 3*fwhm), default=None)
            channels[channel] = {'background': background, 'plane_error': float(error),
                                  'status': 'measured' if background>0 and error<=RING_DIP_REL*background else 'uncertain_background',
                                  'minimum_ring_relative_residual': minimum, 'bins': bins}
        report['profiles'][name] = channels
    return report
