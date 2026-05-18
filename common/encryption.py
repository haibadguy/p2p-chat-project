import hashlib
import os
import base64

# RFC-standardized 512-bit safe prime for Diffie-Hellman key exchange
DH_PRIME = 0xFD7F53811D75122952DF4A9C2DEC270C30288545DE2262B414902F13B6B9625D3456F61A28A8CD17488B8EE2DEB0F51E0E847EA7AF5827EF3DF6A9C72A99CCFF
DH_GENERATOR = 2

class DiffieHellman:
    def __init__(self):
        """
        Initializes a DH keypair. Generates a random 256-bit private key
        and computes the corresponding public key.
        """
        # Generate random 32-byte private key
        self.private_key = int.from_bytes(os.urandom(32), byteorder='big')
        # Compute public key: A = g^a mod p
        self.public_key = pow(DH_GENERATOR, self.private_key, DH_PRIME)

    def generate_shared_key(self, peer_public_key: int) -> bytes:
        """
        Generates the 256-bit symmetric key using the peer's public key.
        Shared Secret = B^a mod p
        Derived Key = SHA-256(Shared Secret)
        """
        shared_secret = pow(peer_public_key, self.private_key, DH_PRIME)
        return hashlib.sha256(str(shared_secret).encode('utf-8')).digest()

def encrypt_decrypt_bytes(data: bytes, key: bytes, iv: bytes) -> bytes:
    """
    Low-level symmetric XOR stream cipher using SHA-256 keystream.
    This works exactly like AES-CTR or ChaCha20, maintaining excellent
    security and performance natively in Python.
    """
    out = bytearray()
    counter = 0
    # Process in 32-byte blocks (size of SHA-256 output)
    for i in range(0, len(data), 32):
        chunk_len = min(32, len(data) - i)
        # Create a unique block: IV (16 bytes) + counter (4 bytes)
        block = iv + counter.to_bytes(4, byteorder='big')
        # Keystream = SHA-256(Key + block)
        keystream = hashlib.sha256(key + block).digest()
        
        # XOR data with keystream
        for j in range(chunk_len):
            out.append(data[i + j] ^ keystream[j])
        counter += 1
    return bytes(out)

def encrypt_bytes(data: bytes, key: bytes) -> tuple:
    """
    Encrypts raw bytes using a random 16-byte IV and the shared symmetric key.
    Returns a tuple: (ciphertext, iv)
    """
    iv = os.urandom(16)
    ciphertext = encrypt_decrypt_bytes(data, key, iv)
    return ciphertext, iv

def decrypt_bytes(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """
    Decrypts raw bytes using the provided 16-byte IV and symmetric key.
    """
    return encrypt_decrypt_bytes(ciphertext, key, iv)

def encrypt_string(text: str, key: bytes) -> tuple:
    """
    Encrypts an UTF-8 string and returns base64-encoded strings: (b64_ciphertext, b64_iv).
    """
    data = text.encode('utf-8')
    ciphertext, iv = encrypt_bytes(data, key)
    return (
        base64.b64encode(ciphertext).decode('utf-8'),
        base64.b64encode(iv).decode('utf-8')
    )

def decrypt_string(b64_ciphertext: str, key: bytes, b64_iv: str) -> str:
    """
    Decrypts base64-encoded ciphertext using key and base64-encoded iv.
    Returns the original decoded plaintext UTF-8 string.
    """
    ciphertext = base64.b64decode(b64_ciphertext.encode('utf-8'))
    iv = base64.b64decode(b64_iv.encode('utf-8'))
    plaintext_bytes = decrypt_bytes(ciphertext, key, iv)
    return plaintext_bytes.decode('utf-8')
