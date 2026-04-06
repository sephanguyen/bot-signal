"""
ZeroClaw Client — Giao tiếp với ZeroClaw agent.

Hỗ trợ 2 mode:
1. CLI: gọi `zeroclaw agent -m "..."` (đơn giản, không cần gateway)
2. HTTP Gateway: gọi localhost gateway API (nhanh hơn, persistent)

ZeroClaw là binary Rust chạy local, không cần cloud.
"""

from __future__ import annotations

import json
import subprocess
import requests
from pydantic import BaseModel

from config import Config


class ZeroClawClient:
    """Client giao tiếp với ZeroClaw agent."""

    def __init__(self):
        self.mode = Config.ZEROCLAW_MODE  # "cli" or "gateway"
        self.gateway_url = Config.ZEROCLAW_GATEWAY_URL
        self.timeout = Config.ZEROCLAW_TIMEOUT

    async def ask(self, system_prompt: str, user_prompt: str, model_class: type[BaseModel] | None = None) -> BaseModel | dict | None:
        """Gửi prompt tới ZeroClaw agent, parse response.

        Args:
            system_prompt: System instruction (skill prompt)
            user_prompt: User message (signal data + JSON schema)
            model_class: Pydantic model để parse JSON response

        Returns:
            Parsed Pydantic model, raw dict, hoặc None nếu lỗi
        """
        json_instruction = ""
        if model_class:
            json_instruction = (
                "\n\nTrả lời CHỈ bằng JSON, không có text khác. Schema:\n"
                + json.dumps(model_class.model_json_schema(), indent=2)
            )

        full_prompt = user_prompt + json_instruction

        if self.mode == "gateway":
            raw = self._call_gateway(system_prompt, full_prompt)
        else:
            raw = self._call_cli(system_prompt, full_prompt)

        if not raw:
            return None

        if model_class:
            return self._parse_json(raw, model_class)

        return {"raw": raw}

    def _call_cli(self, system_prompt: str, user_prompt: str) -> str | None:
        """Gọi ZeroClaw qua CLI subprocess."""
        # Build message với system context
        message = f"[System: {system_prompt}]\n\n{user_prompt}"

        cmd = [
            Config.ZEROCLAW_BIN, "agent",
            "-m", message,
            "--no-stream",  # Output toàn bộ 1 lần
        ]

        if Config.ZEROCLAW_MODEL:
            cmd.extend(["--model", Config.ZEROCLAW_MODEL])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=Config.ZEROCLAW_WORKDIR or None,
            )

            if result.returncode != 0:
                print(f"  ⚠️ ZeroClaw CLI error: {result.stderr[:200]}")
                return None

            return result.stdout.strip()

        except subprocess.TimeoutExpired:
            print(f"  ⚠️ ZeroClaw CLI timeout ({self.timeout}s)")
            return None
        except FileNotFoundError:
            print(f"  ❌ ZeroClaw binary not found: {Config.ZEROCLAW_BIN}")
            print("     Install: https://github.com/zeroclaw-labs/zeroclaw")
            return None
        except Exception as e:
            print(f"  ❌ ZeroClaw CLI error: {e}")
            return None

    def _call_gateway(self, system_prompt: str, user_prompt: str) -> str | None:
        """Gọi ZeroClaw qua HTTP gateway (localhost)."""
        url = f"{self.gateway_url}/v1/chat"

        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        if Config.ZEROCLAW_MODEL:
            payload["model"] = Config.ZEROCLAW_MODEL

        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()

            data = resp.json()

            # ZeroClaw gateway response format
            if "content" in data:
                return data["content"]
            if "message" in data:
                msg = data["message"]
                if isinstance(msg, dict):
                    return msg.get("content", "")
                return str(msg)
            if "choices" in data:
                # OpenAI-compatible format
                return data["choices"][0]["message"]["content"]

            return json.dumps(data)

        except requests.ConnectionError:
            print(f"  ❌ ZeroClaw gateway not reachable: {self.gateway_url}")
            print("     Start gateway: zeroclaw gateway")
            return None
        except requests.Timeout:
            print(f"  ⚠️ ZeroClaw gateway timeout ({self.timeout}s)")
            return None
        except Exception as e:
            print(f"  ❌ ZeroClaw gateway error: {e}")
            return None

    def _parse_json(self, raw: str, model_class: type[BaseModel]) -> BaseModel | None:
        """Parse JSON từ response text → Pydantic model."""
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
                return model_class.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            print(f"  ⚠️ ZeroClaw JSON parse error: {e}")
        return None

    def health_check(self) -> dict:
        """Kiểm tra ZeroClaw có sẵn sàng không."""
        result = {"available": False, "mode": self.mode, "version": None}

        if self.mode == "gateway":
            try:
                resp = requests.get(
                    f"{self.gateway_url}/health",
                    timeout=5,
                )
                if resp.ok:
                    result["available"] = True
                    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    result["version"] = data.get("version")
            except Exception:
                pass

        # Always check CLI as fallback
        try:
            proc = subprocess.run(
                [Config.ZEROCLAW_BIN, "status"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                result["available"] = True
                result["cli_status"] = proc.stdout.strip()[:200]
        except Exception:
            pass

        return result
