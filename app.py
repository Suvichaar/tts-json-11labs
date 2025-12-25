import streamlit as st
import json
import uuid
import os
import requests
import boto3
import csv
import io
from datetime import datetime

# === SECRETS FROM STREAMLIT ===
# Try to get ElevenLabs API key from secrets, with fallback
try:
    ELEVENLABS_API_KEY = st.secrets.get("elevenlabs", {}).get("ELEVENLABS_API_KEY") or st.secrets.get("elevenlabs", {}).get("api_key", "")
except:
    ELEVENLABS_API_KEY = ""

# AWS Configuration from secrets
try:
    AWS_ACCESS_KEY = st.secrets["aws"]["AWS_ACCESS_KEY"]
    AWS_SECRET_KEY = st.secrets["aws"]["AWS_SECRET_KEY"]
    AWS_REGION = st.secrets["aws"]["AWS_REGION"]
    AWS_BUCKET = st.secrets["aws"]["AWS_BUCKET"]
    S3_PREFIX = st.secrets["aws"]["S3_PREFIX"]
    CDN_BASE = st.secrets["aws"]["CDN_BASE"]
except KeyError as e:
    st.error(f"❌ Missing AWS configuration in secrets: {e}")
    st.stop()

# === STREAMLIT UI ===
st.title("🎙️ ElevenLabs Text-to-Speech to S3")
uploaded_file = st.file_uploader("Upload JSON file", type=["json"])

# ElevenLabs Configuration
st.markdown("### ⚙️ ElevenLabs Configuration")

# API Key is read from secrets only (not exposed in UI)
elevenlabs_api_key = ELEVENLABS_API_KEY

# Get default Voice ID from secrets
try:
    default_voice_id = st.secrets.get("elevenlabs", {}).get("ELEVENLABS_VOICE_ID") or st.secrets.get("elevenlabs", {}).get("voice_id", "")
except:
    default_voice_id = ""

# Voice ID can be configured by user
voice_id = st.text_input(
    "Voice ID",
    value=default_voice_id,
    help="Enter your ElevenLabs Voice ID (e.g., yD0Zg2jxgfQLY8I2MEHO). Default is read from secrets if configured."
)

# Model ID can be configured by user
model_id = st.text_input(
    "Model ID",
    value="eleven_multilingual_v2",
    help="Enter ElevenLabs Model ID (default: eleven_multilingual_v2). Options: eleven_multilingual_v2, eleven_monolingual_v1, etc."
)

# === TTS + UPLOAD ===
def synthesize_and_upload(paragraphs, voice_id, model_id, api_key):
    s3 = boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        region_name=AWS_REGION,
    )

    result = {}
    os.makedirs("temp", exist_ok=True)

    index = 2  # Start from slide2, s2paragraph1, audio_url2
    for text in paragraphs.values():
        st.write(f"🛠️ Processing: slide{index}")

        # ElevenLabs API call
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.5
            }
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            st.error(f"❌ HTTP Error {e.response.status_code} for slide{index}")
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                try:
                    error_detail = e.response.json()
                    st.error(f"Error details: {error_detail}")
                except:
                    st.error(f"Response: {e.response.text}")
            continue
        except requests.exceptions.RequestException as e:
            st.error(f"❌ Error generating TTS for slide{index}: {e}")
            continue

        filename = f"tts_{uuid.uuid4().hex}.mp3"
        local_path = os.path.join("temp", filename)

        with open(local_path, "wb") as f:
            f.write(response.content)

        # Fix S3 key path (handle trailing slashes)
        s3_key = f"{S3_PREFIX.rstrip('/')}/{filename}"
        s3.upload_file(local_path, AWS_BUCKET, s3_key)
        cdn_url = f"{CDN_BASE.rstrip('/')}/{s3_key}"

        # Build result dict
        slide_key = f"slide{index}"
        paragraph_key = f"s{index}paragraph1"
        audio_key = f"audio_url{index}"

        result[slide_key] = {
            paragraph_key: text,
            audio_key: cdn_url,
            "voice_id": voice_id,
            "model_id": model_id
        }

        index += 1
        os.remove(local_path)

    return result

# === CSV GENERATION ===
def generate_csv_links(output):
    """Generate CSV with only the audio links"""
    output_csv = io.StringIO()
    writer = csv.writer(output_csv)
    
    # Write header
    writer.writerow(["Link"])
    
    # Extract links from output
    for slide_key in sorted(output.keys(), key=lambda x: int(x.replace("slide", ""))):
        slide_data = output[slide_key]
        # Find audio_url key (e.g., audio_url2, audio_url3, etc.)
        for key, value in slide_data.items():
            if key.startswith("audio_url"):
                writer.writerow([value])
                break
    
    return output_csv.getvalue()

# === MAIN EXECUTION ===
# Initialize session state for output and timestamp
if 'tts_output' not in st.session_state:
    st.session_state.tts_output = None
if 'tts_timestamp' not in st.session_state:
    st.session_state.tts_timestamp = None

if uploaded_file:
    paragraphs = json.load(uploaded_file)
    st.success(f"✅ Loaded {len(paragraphs)} paragraphs")

    # Validate inputs
    if not elevenlabs_api_key:
        st.error("❌ ElevenLabs API Key is required. Please add it to .streamlit/secrets.toml under [elevenlabs] section")
    elif not voice_id:
        st.warning("⚠️ Please enter a Voice ID")
    elif not model_id:
        st.warning("⚠️ Please enter a Model ID")
    else:
        if st.button("🚀 Generate TTS + Upload to S3"):
            with st.spinner("Generating TTS and uploading to S3..."):
                try:
                    output = synthesize_and_upload(paragraphs, voice_id, model_id, elevenlabs_api_key)
                    
                    if output:
                        # Store output and timestamp in session state
                        st.session_state.tts_output = output
                        st.session_state.tts_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        st.success(f"✅ Done! Generated {len(output)} audio files and uploaded to S3!")
                    else:
                        st.error("❌ No audio files were generated. Please check the errors above.")
                        st.session_state.tts_output = None
                        st.session_state.tts_timestamp = None
                except Exception as e:
                    st.error(f"❌ Error: {e}")
                    import traceback
                    st.code(traceback.format_exc())
                    st.session_state.tts_output = None
                    st.session_state.tts_timestamp = None
        
        # Display download buttons if output exists in session state
        if st.session_state.tts_output and st.session_state.tts_timestamp:
            st.markdown("---")
            st.markdown("### 📥 Download Files")
            col1, col2 = st.columns(2)
            
            timestamp = st.session_state.tts_timestamp
            
            with col1:
                st.download_button(
                    label="⬇️ Download Output JSON",
                    data=json.dumps(st.session_state.tts_output, indent=2, ensure_ascii=False),
                    file_name=f"Output_data_{timestamp}.json",
                    mime="application/json"
                )
            
            with col2:
                csv_data = generate_csv_links(st.session_state.tts_output)
                st.download_button(
                    label="⬇️ Download Links CSV",
                    data=csv_data,
                    file_name=f"audio_links_{timestamp}.csv",
                    mime="text/csv"
                )
