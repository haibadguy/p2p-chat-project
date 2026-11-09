import hashlib
import os
import base64

# RFC 3526, section 3: 2048-bit MODP Group (group 14).
# p is a safe prime (p = 2q + 1 with q prime), so g = 2 generates the
# subgroup of order q, which is the standard choice for this group.
DH_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16,
)
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
    Low-level symmetric XOR stream cipher using a SHA-256 keystream.

    The construction is structurally similar to a stream cipher such as
    AES-CTR or ChaCha20, but this is a teaching implementation: it has no
    MAC/AEAD tag and no peer authentication, so it does not provide
    ciphertext integrity. Do not use it for production or sensitive data.
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
