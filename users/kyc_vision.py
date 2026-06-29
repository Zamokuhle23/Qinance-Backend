from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile

from .models import KYCDocument, KYCVerification


MODEL_DIR = Path(settings.BASE_DIR) / 'kyc_models'
DETECTOR_MODEL = MODEL_DIR / 'face_detection_yunet_2023mar.onnx'
RECOGNIZER_MODEL = MODEL_DIR / 'face_recognition_sface_2021dec.onnx'


def correct_id_document(uploaded_file):
    """Detect and perspective-correct a web camera ID capture on the server."""
    import cv2
    import numpy as np

    uploaded_file.seek(0)
    raw = uploaded_file.read()
    uploaded_file.seek(0)
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('The ID photo could not be read. Please retake it.')

    height, width = image.shape[:2]
    detection_width = min(width, 1100)
    scale = detection_width / width
    detection = cv2.resize(image, (detection_width, round(height * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(detection, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 55, 160)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_area = 0
    minimum_area = detection.shape[0] * detection.shape[1] * 0.16
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        area = abs(cv2.contourArea(polygon))
        if len(polygon) == 4 and area > minimum_area and area > best_area:
            best, best_area = polygon, area
    correction_method = 'detected_perspective'
    if best is None:
        # The web camera places a visible guide around the ID. Real cards often
        # have rounded corners, glare, or low-contrast edges, so a missing
        # four-corner contour must not trap an applicant in a retake loop.
        # Keep a generous margin around that guide for manual review.
        margin_x, margin_y = width * 0.12, height * 0.12
        points = np.array([
            [margin_x, margin_y], [width - margin_x, margin_y],
            [width - margin_x, height - margin_y], [margin_x, height - margin_y],
        ], dtype='float32')
        correction_method = 'guide_frame_crop'
    else:
        points = best.reshape(4, 2).astype('float32') / scale
    ordered = np.zeros((4, 2), dtype='float32')
    point_sums = points.sum(axis=1)
    point_differences = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[np.argmin(point_sums)]       # top-left
    ordered[2] = points[np.argmax(point_sums)]       # bottom-right
    ordered[1] = points[np.argmin(point_differences)] # top-right
    ordered[3] = points[np.argmax(point_differences)] # bottom-left
    top_left, top_right, bottom_right, bottom_left = ordered
    output_width = round(max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left)))
    output_height = round(max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right)))
    if output_width < 240 or output_height < 140:
        raise ValueError('The ID photo resolution is too small. Move closer and retake it.')

    destination = np.array([
        [0, 0], [output_width - 1, 0],
        [output_width - 1, output_height - 1], [0, output_height - 1],
    ], dtype='float32')
    transform = cv2.getPerspectiveTransform(ordered, destination)
    corrected = cv2.warpPerspective(image, transform, (output_width, output_height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    encoded, jpeg = cv2.imencode('.jpg', corrected, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not encoded:
        raise ValueError('The corrected ID could not be encoded. Please retake it.')
    return ContentFile(jpeg.tobytes(), name='corrected-id.jpg'), {
        'server_corrected': True,
        'correction_method': correction_method,
        'source_size': [width, height],
        'corrected_size': [output_width, output_height],
    }


def _read_image(document):
    import cv2
    import numpy as np

    document.file.open('rb')
    try:
        data = document.file.read()
    finally:
        document.file.close()
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def _detect(detector, image):
    import cv2

    if image is None:
        return None, {'error': 'unreadable_image'}
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)
    if faces is None or len(faces) != 1:
        return None, {'error': 'one_face_required', 'faces': 0 if faces is None else len(faces)}
    face = faces[0]
    x, y, w, h = face[:4]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    face_ratio = float((w * h) / (width * height))
    right_eye_x, left_eye_x, nose_x = float(face[4]), float(face[6]), float(face[8])
    eye_distance = max(abs(left_eye_x - right_eye_x), 1.0)
    eye_midpoint = (left_eye_x + right_eye_x) / 2.0
    yaw_proxy = float((nose_x - eye_midpoint) / eye_distance)
    return face, {
        'blur_score': round(blur, 2),
        'face_area_ratio': round(face_ratio, 4),
        'yaw_proxy': round(yaw_proxy, 4),
        'quality_passed': blur >= 35 and face_ratio >= 0.08,
    }


def evaluate_user_kyc(user):
    """Create advisory KYC evidence. Never approves or rejects an account."""
    verification, _ = KYCVerification.objects.get_or_create(user=user)
    try:
        import cv2
    except ImportError:
        verification.details = {'error': 'opencv_not_installed'}
        verification.recommendation = 'manual_review'
        verification.save()
        return verification

    if not DETECTOR_MODEL.exists() or not RECOGNIZER_MODEL.exists():
        verification.details = {'error': 'vision_models_not_installed'}
        verification.recommendation = 'manual_review'
        verification.save()
        return verification

    documents = {item.document_type: item for item in user.documents.all()}
    required = ('id', 'selfie_front', 'selfie_left', 'selfie_right')
    missing = [kind for kind in required if kind not in documents]
    if missing:
        verification.details = {'missing': missing}
        verification.recommendation = 'manual_review'
        verification.save()
        return verification

    detector = cv2.FaceDetectorYN.create(str(DETECTOR_MODEL), '', (320, 320), 0.8, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER_MODEL), '')
    evidence = {}
    detected = {}
    images = {}
    for kind in required:
        images[kind] = _read_image(documents[kind])
        detected[kind], evidence[kind] = _detect(detector, images[kind])

    pose_passed = (
        detected['selfie_front'] is not None
        and detected['selfie_left'] is not None
        and detected['selfie_right'] is not None
        and abs(evidence['selfie_front'].get('yaw_proxy', 1)) <= 0.18
        and evidence['selfie_left'].get('yaw_proxy', 0) <= -0.16
        and evidence['selfie_right'].get('yaw_proxy', 0) >= 0.16
        and all(evidence[kind].get('quality_passed') for kind in ('selfie_front', 'selfie_left', 'selfie_right'))
    )

    similarity = None
    if detected['id'] is not None and detected['selfie_front'] is not None:
        id_face = recognizer.alignCrop(images['id'], detected['id'])
        selfie_face = recognizer.alignCrop(images['selfie_front'], detected['selfie_front'])
        id_feature = recognizer.feature(id_face)
        selfie_feature = recognizer.feature(selfie_face)
        similarity = float(recognizer.match(id_feature, selfie_feature, cv2.FaceRecognizerSF_FR_COSINE))

    if similarity is not None and similarity >= 0.45 and pose_passed:
        recommendation = 'likely_match'
    elif similarity is not None and similarity < 0.30:
        recommendation = 'needs_attention'
    else:
        recommendation = 'manual_review'

    verification.similarity_score = similarity
    verification.pose_challenge_passed = pose_passed
    verification.recommendation = recommendation
    verification.details = evidence
    verification.save()
    return verification
