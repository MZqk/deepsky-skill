"""成片 JPEG 编码契约。

`skimage.io.imsave` 走 PIL 默认 quality=75，实测成片 qtable 为
[8,6,5,8,12,20,26,31]；quality=95 为 [2,1,1,2,2,4,5,6]。8.3MP 密集星场
叠加微弱渐变时 Q75 会在暗部与星点周围产生带状。

`subsampling=0`（4:4:4）不是画质偏好而是硬要求：4:2:0 会把 3px 彩色星点的
色度误差放大到 maxerr=76（实测），4:4:4 为 18，体积只多 24%。天文成片里
星点是最重要的内容。
"""

from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from fits_io import read_image, write_image  # noqa: E402

pytest.importorskip('PIL')

QTABLE_Q95 = [2, 1, 1, 2, 2, 4, 5, 6]


def _qtable(path):
    from PIL import Image
    with Image.open(path) as handle:
        return list(handle.quantization[0])[:8]


def _gradient(size=64):
    """平滑亮度梯度 —— Q75 的带状伪影最容易在这种输入上暴露。"""
    row = np.linspace(0, 1, size, dtype=np.float32)
    return np.repeat(row[None, :], size, axis=0)


def test_jpeg_uses_q95_not_skimage_default_q75(tmp_path):
    path = tmp_path / 'final.jpg'
    write_image(np.repeat(_gradient()[..., None], 3, axis=2), str(path))
    assert _qtable(path) == QTABLE_Q95


def test_jpeg_uses_4_4_4_chroma_to_protect_star_colour(tmp_path):
    """4:2:0 把 3px 彩色星点的色度误差放大到 maxerr=76，4:4:4 为 18。"""
    from PIL import Image

    image = np.zeros((64, 64, 3), np.uint8)
    image[30:33, 30:33] = (255, 120, 40)
    path = tmp_path / 'star.jpg'
    write_image(image.astype(np.float32) / 255.0, str(path))

    with Image.open(path) as handle:
        # 非 4:2:0 的直接判据：分量数 3（4:2:0 只有单个 Y 分量）
        assert len(handle.layer) == 3
        back = np.asarray(handle).astype(np.int32)
    star = slice(29, 34)
    assert int(np.abs(back[star, star] - image[star, star]).max()) <= 24


def test_jpeg_carries_srgb_icc_profile(tmp_path):
    from PIL import Image

    path = tmp_path / 'icc.jpg'
    write_image(np.full((32, 32, 3), 0.5, np.float32), str(path))
    with Image.open(path) as handle:
        assert (handle.info.get('icc_profile') or b'') != b''


def test_png_output_is_unchanged_by_the_jpeg_branch(tmp_path):
    """PNG 是无损格式，quality/subsampling/ICC 都不适用，必须仍走原 skimage 路径。

    PNG 本身是 8bit 量化格式，所以这里比对的期望值是量化后的网格，
    而不是原始浮点输入 —— 要证明的是「编码参数没有影响 PNG」，
    不是「PNG 无损于浮点输入」。
    """
    image = np.repeat(_gradient(32)[..., None], 3, axis=2)
    path = tmp_path / 'frame.png'
    write_image(image, str(path))
    out, _ = read_image(str(path))
    expected = (image * 255).astype(np.uint8).astype(np.float32) / 255.0
    np.testing.assert_array_equal(out, expected)


def test_grayscale_jpeg_keeps_single_channel(tmp_path):
    """agent_workflow 的 mask_preview 是 2D 掩膜，走 JPEG 的 'L' 模式。"""
    path = tmp_path / 'mask.jpg'
    write_image(np.full((16, 16), 0.25, np.float32), str(path))
    out, _ = read_image(str(path))
    assert out.ndim == 2


def test_uint8_conversion_matches_previous_truncation(tmp_path):
    """`.astype(np.uint8)` 而非 round：与既有 skimage 路径逐位一致。

    改成 round 会让所有 JPG 输入的像素值变化 1 LSB。
    """
    values = np.array([[[0.0], [0.5 / 255], [1.4 / 255], [1.6 / 255]]], dtype=np.float32)
    path = tmp_path / 'trunc.jpg'
    write_image(np.repeat(values, 3, axis=2), str(path))
    out, _ = read_image(str(path))
    # 0.5/255*255=0.5 → 截断为 0；1.4/255*255=1.4 → 1；1.6/255*255=1.6 → 1
    assert (out[0, :, 0] * 255).round().astype(int).tolist() == [0, 0, 1, 1]
