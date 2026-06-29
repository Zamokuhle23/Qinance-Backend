from pathlib import Path

from django.conf import settings

from .models import KYCDocument, KYCVerification


MODEL_DIR = Path(settings.BASE_DIR) / 'kyc_models'
DETECTOR_MODEL = MODEL_DIR / 'face_detection_yunet_2023mar.onnx'
RECOGNIZER_MODEL = MODEL_DIR / 'face_recognition_sface_2021dec.onnx'


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
