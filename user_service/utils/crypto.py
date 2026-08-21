import hashlib
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

load_dotenv()

JWT_TOKEN = os.getenv("JWT_TOKEN")

def decrypt_nonce(received_nonce):
    try:
        iv_hex, ciphertext_hex = received_nonce.split('.')
        iv = bytes.fromhex(iv_hex)
        encrypted_data = bytes.fromhex(ciphertext_hex)

        key = hashlib.sha256(JWT_TOKEN.encode()).digest()
        
        aesgcm = AESGCM(key)
        
        decrypted_bytes = aesgcm.decrypt(iv, encrypted_data, None)
        decrypted_str = decrypted_bytes.decode('utf-8')

        user_id, timestamp_str, random = decrypted_str.split('.')
        
        return {
            "userId": user_id,
            "timestamp": int(timestamp_str),
            "random": random
        }
    except Exception as e:
        logger.warning("Nonce decryption failed", exc_info=True)
        return None
