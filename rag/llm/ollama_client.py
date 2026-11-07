import json
from urllib.request import Request, urlopen

class OllamaClient:
    def __init__(
            self,
            model: str,
            base_url: str,
            provider: str = "local",
            api_key: str | None = None,
            timeout: float = 120.0,
    ):
        self.model = model
        self.base_url = base_url
        self.provider = provider
        self.api_key = api_key
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        # payload를 서버로 전송하기
        url = self.base_url.rstrip("/") + "/api/chat"

        headers = {"Content-Type": "application/json"}

        if self.provider == "cloud":    # cloud 모드인지 확인
            if not self.api_key or not self.api_key.strip():    # 키가 없으면 전송 전에 오류 발생
                raise ValueError("클라우드 모드에는 API 키가 필요합니다.")
            headers["Authorization"] = f"Bearer {self.api_key}" # 키가 있으면 인증 헤더 추가

        # Payload를 전송할 수 있는 데이터로 바꾸기
        # urllib로 전송하려면 바이트로 바꿔야하기 때문에 json.dumps() 직렬화로 수행
        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

        request = Request(url=url, data=body, headers=headers, method="POST")

        # 실제로 전송하고 응답 읽기
        with urlopen(request, timeout=self.timeout) as response:
            response_bytes = response.read()
            response_text = response_bytes.decode("utf-8")
            response_data = json.loads(response_text)

            answer_text = response_data["message"]["content"]

        if not isinstance(answer_text, str) or not answer_text.strip():
            raise ValueError("모델이 비어 있지 않은 답변 문자열을 반환하지 않았습니다.")

        return answer_text