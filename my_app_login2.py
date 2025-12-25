import streamlit as st
import requests
import json

# ======================
# 設定
# ======================
DIFY_API_KEY = st.secrets["DIFY_API_KEY"]
DIFY_URL = "https://api.dify.ai/v1/chat-messages"

headers = {
    "Authorization": f"Bearer {DIFY_API_KEY}",
    "Content-Type": "application/json",
}

st.title("Dify Streaming Test")

# セッション初期化
if "messages" not in st.session_state:
    st.session_state.messages = []

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None  # ← 空文字は禁止

# ======================
# 過去ログ表示
# ======================
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ======================
# ユーザー入力
# ======================
user_input = st.chat_input("メッセージを入力")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_response = ""

        # ---- Dify送信データ（最小構成）----
        data = {
            "query": user_input,
            "response_mode": "streaming",
            "user": "streamlit-user"
        }

        if st.session_state.conversation_id:
            data["conversation_id"] = st.session_state.conversation_id

        # ---- APIコール ----
        response = requests.post(
            DIFY_URL,
            headers=headers,
            json=data,
            stream=True,
            timeout=60
        )

        # ★ ここ超重要：HTTPエラー確認
        if response.status_code != 200:
            st.error(f"Dify API Error: {response.status_code}")
            st.text(response.text)
            st.stop()

        # ---- Streaming処理 ----
        for line in response.iter_lines():
            if not line:
                continue

            decoded = line.decode("utf-8")

            if not decoded.startswith("data:"):
                continue

            chunk = json.loads(decoded.replace("data: ", ""))

            # conversation_id 保存
            if "conversation_id" in chunk:
                st.session_state.conversation_id = chunk["conversation_id"]

            event = chunk.get("event")

            # 通常チャット
            if event == "message":
                full_response += chunk.get("answer", "")
                placeholder.markdown(full_response + "▌")

            # Chatflow
            elif event == "text_chunk":
                full_response += chunk["data"].get("text", "")
                placeholder.markdown(full_response + "▌")

            # 終了
            elif event in ("message_end", "workflow_finished"):
                break

        placeholder.markdown(full_response)
        st.session_state.messages.append(
            {"role": "assistant", "content": full_response}
        )
