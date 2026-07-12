from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DegradationProfile:
    name: str
    dpi: int = 200
    rotation: int = 0
    skew_degrees: float = 0.0
    blur_sigma: float = 0.0
    contrast: float = 1.0
    noise_stddev: float = 0.0
    jpeg_quality: int = 75

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_DEGRADATION_PROFILES = (
    DegradationProfile(name="scan-200dpi", dpi=200, jpeg_quality=80),
    DegradationProfile(
        name="low-contrast-skew",
        dpi=170,
        skew_degrees=1.5,
        blur_sigma=0.7,
        contrast=0.72,
        noise_stddev=3.0,
        jpeg_quality=65,
    ),
    DegradationProfile(name="rotated-90", dpi=200, rotation=90, jpeg_quality=75),
)


def degrade_pdf_to_image_pdf(
    content: bytes,
    *,
    profile: DegradationProfile,
    seed: int = 1,
) -> bytes:
    try:
        import cv2  # type: ignore[import-not-found]
        import fitz  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Install PyMuPDF, NumPy, and headless OpenCV to generate degraded PDFs.") from exc

    source = fitz.open(stream=content, filetype="pdf")
    output = fitz.open()
    rng = np.random.default_rng(seed)
    try:
        for page_index in range(source.page_count):
            page = source.load_page(page_index)
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(profile.dpi / 72.0, profile.dpi / 72.0),
                alpha=False,
                colorspace=fitz.csRGB,
            )
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height,
                pixmap.width,
                3,
            )
            transformed = _degrade_image(image, profile=profile, rng=rng, cv2=cv2, np=np)
            success, encoded = cv2.imencode(
                ".jpg",
                cv2.cvtColor(transformed, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, max(20, min(100, profile.jpeg_quality))],
            )
            if not success:
                raise RuntimeError(f"Could not encode degraded page {page_index + 1}.")

            width, height = float(page.rect.width), float(page.rect.height)
            if profile.rotation % 180:
                width, height = height, width
            output_page = output.new_page(width=width, height=height)
            output_page.insert_image(output_page.rect, stream=encoded.tobytes())
        return output.tobytes(deflate=True, garbage=4)
    finally:
        output.close()
        source.close()


def _degrade_image(image: object, *, profile: DegradationProfile, rng: object, cv2: object, np: object) -> object:
    transformed = image
    rotation = profile.rotation % 360
    if rotation == 90:
        transformed = cv2.rotate(transformed, cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        transformed = cv2.rotate(transformed, cv2.ROTATE_180)
    elif rotation == 270:
        transformed = cv2.rotate(transformed, cv2.ROTATE_90_COUNTERCLOCKWISE)

    if profile.skew_degrees:
        height, width = transformed.shape[:2]
        matrix = cv2.getRotationMatrix2D(
            (width / 2, height / 2),
            profile.skew_degrees,
            1.0,
        )
        transformed = cv2.warpAffine(
            transformed,
            matrix,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )
    if profile.blur_sigma > 0:
        transformed = cv2.GaussianBlur(transformed, (0, 0), profile.blur_sigma)
    if profile.contrast != 1.0:
        transformed = cv2.convertScaleAbs(transformed, alpha=profile.contrast, beta=255 * (1 - profile.contrast))
    if profile.noise_stddev > 0:
        noise = rng.normal(0, profile.noise_stddev, transformed.shape)
        transformed = np.clip(transformed.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return transformed
