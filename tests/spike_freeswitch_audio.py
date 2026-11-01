import os
import io
import wave
import struct
import socket
import logging
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("spike_probe")

def probe_esl_connection(host: str = "127.0.0.1", port: int = 8021, password: str = "ClueCon") -> dict:
    result = {"host": host, "port": port, "connected": False, "authenticated": False, "status_summary": None}
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect((host, port))
        result["connected"] = True
        banner = s.recv(1024).decode(errors="ignore")
        s.sendall(f"auth {password}\n\n".encode())
        auth_resp = s.recv(1024).decode(errors="ignore")
        if "+OK accepted" in auth_resp:
            result["authenticated"] = True
            s.sendall(b"api status\n\n")
            stat = s.recv(4096).decode(errors="ignore")
            result["status_summary"] = stat.split("\n")[0] if stat else "Connected"
    except Exception as e:
        result["error"] = str(e)
    finally:
        s.close()
    return result

def generate_test_stereo_wav(sample_rate: int = 16000, duration_sec: float = 1.0) -> bytes:
    """
    Generates a 16-bit PCM stereo WAV where:
    - Channel 0 (A-leg caller) has a 440 Hz tone (audible RMS ~7000).
    - Channel 1 (B-leg callee) has digital silence (RMS 0).
    """
    total_samples = int(sample_rate * duration_sec)
    t = np.linspace(0, duration_sec, total_samples, endpoint=False)
    # Sine wave with amplitude ~10000 on ch0, 0 on ch1
    ch0 = (10000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    ch1 = np.zeros(total_samples, dtype=np.int16)

    # Interleave: [ch0_0, ch1_0, ch0_1, ch1_1, ...]
    interleaved = np.empty((total_samples * 2,), dtype=np.int16)
    interleaved[0::2] = ch0
    interleaved[1::2] = ch1

    raw_bytes = interleaved.tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw_bytes)
    return buf.getvalue()

def parse_wav_header(wav_bytes: bytes) -> dict:
    """
    Parses standard RIFF WAV header to find audio format, channels, sample rate,
    bits per sample, and byte offset of the audio 'data' chunk.
    Works even on live growing files where data length might be 0xFFFFFFFF or unfinalized.
    """
    if len(wav_bytes) < 44 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise ValueError("Not a valid RIFF/WAVE container")

    offset = 12
    fmt_found = False
    channels = 2
    sample_rate = 8000
    bits_per_sample = 16
    data_offset = 44
    data_size = len(wav_bytes) - 44

    while offset + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[offset:offset+4]
        chunk_size = struct.unpack("<I", wav_bytes[offset+4:offset+8])[0]
        offset += 8
        if chunk_id == b"fmt ":
            fmt_found = True
            audio_fmt, channels, sample_rate, byte_rate, block_align, bits_per_sample = struct.unpack(
                "<HHIIHH", wav_bytes[offset:offset+16]
            )
            offset += chunk_size
        elif chunk_id == b"data":
            data_offset = offset
            data_size = chunk_size
            break
        else:
            offset += chunk_size

    return {
        "channels": channels,
        "sample_rate": sample_rate,
        "bits_per_sample": bits_per_sample,
        "data_offset": data_offset,
        "data_size": data_size
    }

def verify_channel_isolation_and_rms():
    wav_data = generate_test_stereo_wav(sample_rate=16000, duration_sec=1.0)
    info = parse_wav_header(wav_data)
    
    pcm_payload = wav_data[info["data_offset"]:]
    # Must be whole frames (4 bytes per 16-bit stereo frame)
    frame_bytes = (len(pcm_payload) // 4) * 4
    samples = np.frombuffer(pcm_payload[:frame_bytes], dtype=np.int16)
    
    ch0 = samples[0::2]
    ch1 = samples[1::2]
    
    rms0 = float(np.sqrt(np.mean(ch0.astype(np.float64) ** 2)))
    rms1 = float(np.sqrt(np.mean(ch1.astype(np.float64) ** 2)))
    
    return {
        "parsed_header": info,
        "ch0_rms": rms0,
        "ch1_rms": rms1,
        "isolation_verified": (rms0 > 5000 and rms1 == 0.0)
    }

if __name__ == "__main__":
    logger.info("=== STEP 0 TECHNICAL SPIKE & VERIFICATION PROBE ===")
    
    # 1. ESL Probe
    esl_res = probe_esl_connection(host=os.getenv("FREESWITCH_ESL_HOST", "127.0.0.1"))
    logger.info(f"ESL Connection Probe: {esl_res}")
    
    # 2. Audio Channel Isolation & Header Parsing Probe
    audio_res = verify_channel_isolation_and_rms()
    logger.info(f"Stereo Audio Isolation & RMS Probe: {audio_res}")
    
    # 3. Environment Credential Checks
    google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    eleven_key = os.getenv("ELEVENLABS_API_KEY", "")
    logger.info(f"Google Credentials path configured: {'YES' if google_creds else 'NO'} ({google_creds})")
    logger.info(f"ElevenLabs API Key configured: {'YES' if eleven_key else 'NO'}")
    
    assert audio_res["isolation_verified"] is True, "Audio channel separation failed"
    logger.info("Step 0 Audio De-interleaving and RMS Spike PASSED.")
