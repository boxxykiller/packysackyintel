"""Windows built-in OCR (Windows.Media.Ocr) for PIL images."""
import asyncio
import threading

from PIL import Image
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

_lock = threading.Lock()      # the engine can only run one recognition at a time
_engine = OcrEngine.try_create_from_user_profile_languages()


async def _recognize(img):
    img = img.convert("RGBA")
    # Windows OCR is a dark-on-light engine at heart but handles light-on-dark; upscale helps small text.
    w = DataWriter()
    r, g, b, a = img.split()
    w.write_bytes(bytearray(Image.merge("RGBA", (b, g, r, a)).tobytes()))
    bmp = SoftwareBitmap.create_copy_with_alpha_from_buffer(
        w.detach_buffer(), BitmapPixelFormat.BGRA8, img.width, img.height, BitmapAlphaMode.PREMULTIPLIED)
    result = await _engine.recognize_async(bmp)
    return [[(w.text, w.bounding_rect.x, w.bounding_rect.y, w.bounding_rect.width, w.bounding_rect.height)
             for w in line.words] for line in result.lines]


MAX_DIM = OcrEngine.max_image_dimension


def _run(img):
    with _lock:
        return asyncio.run(_recognize(img))


def recognize(img, scale=1):
    """OCR an image. Returns lines, each a list of words (text, x, y, w, h) in original pixel coordinates."""
    if scale != 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return [[(t, x / scale, y / scale, w / scale, h / scale) for t, x, y, w, h in line]
            for line in _run(img) if line]


def read_lines(img, scale=3):
    """Returns [(text, top_px, bottom_px)] with pixel rows in the original (unscaled) image."""
    return [(" ".join(w[0] for w in line), min(w[2] for w in line), max(w[2] + w[4] for w in line))
            for line in recognize(img, scale)]
