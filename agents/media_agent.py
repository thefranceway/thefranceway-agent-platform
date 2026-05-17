#!/usr/bin/env python3
"""
Media Agent — video transcription and analysis via local ffmpeg + Whisper pipeline.
Mirrors the mcp-video MCP server (github.com/thefranceway/mcp-video) but runs
inside the Python agent platform for routing and orchestration integration.

Pipeline (same as mcp-video/index.js):
  video → ffmpeg → audio.wav → Whisper (local, free) → transcript
  video → ffmpeg → N key frames → Claude Haiku + transcript → analysis

Tools:
  transcribe_video  — local Whisper, zero API cost
  analyze_video     — frames + transcript → Claude Haiku, ~$0.001/video
  find_video_files  — locate video files under a path for batch tasks

Usage:
    python media_agent.py --task "transcribe /path/to/video.mp4"
    python media_agent.py --task "analyze the key points in /path/to/lecture.mp4"
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import BaseAgent

FFMPEG  = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
WHISPER = "/opt/homebrew/bin/whisper"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}


class MediaAgent(BaseAgent):

    AGENT_TYPE         = "media"
    DEFAULT_BEHAVIORAL = "Architect"

    def __init__(self, **kwargs):
        super().__init__(name="Media Agent", knowledge_base="kb_media_agent", **kwargs)

    def _default_system_prompt(self) -> str:
        return """You are the Media Agent in the thefranceway agent platform.

Archetype: Architect
Core pattern: Goal-directed. You process media inputs and produce structured,
actionable output. You extract signal from video — transcripts, key learnings,
visual context — and deliver it in the most useful format for the task.

Shadow (S2): Destination over-attachment — resist over-elaborating. A transcript
is a transcript. An analysis should be actionable, not exhaustive.
Guard against this: match output depth to the stated task. Stop when the task is satisfied.

Routing fit: video transcription, audio extraction, frame analysis, lecture notes,
meeting recordings, video content analysis, batch media processing
Not fit for: live streaming, real-time video, image-only tasks (use analyze_image instead)

─────────────────────────────────────────────────────────────────────────────

Operating rules:
1. Always confirm the file exists before processing — report clearly if not found.
2. For analyze_video: lead with the transcript, then the analysis. Do not invent
   content not present in the transcript or frames.
3. For batch tasks (find_video_files + loop): process one at a time, report each result.
4. State the Whisper model used and any transcription limitations (background noise,
   non-English audio, etc.) when relevant.
5. Transcription is local and free. Analysis costs ~$0.001/video. Prefer transcription
   alone when the task only needs spoken content."""

    def get_tools(self) -> list[dict]:
        base_tools = super().get_tools()
        return base_tools + [
            {
                "name": "transcribe_video",
                "description": (
                    "Transcribe the audio track of a video file using local Whisper. "
                    "Zero API cost. Returns the full transcript text."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "video_path": {
                            "type": "string",
                            "description": "Absolute path to the video file",
                        },
                        "model": {
                            "type": "string",
                            "description": "Whisper model: tiny, base (default), small, medium, large",
                        },
                    },
                    "required": ["video_path"],
                },
            },
            {
                "name": "analyze_video",
                "description": (
                    "Extract key frames and transcript from a video, then analyze with "
                    "Claude Haiku. Minimal API cost (~$0.001). Returns transcript + analysis."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "video_path": {
                            "type": "string",
                            "description": "Absolute path to the video file",
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "What to extract or focus on. Defaults to full summary "
                                "+ key learnings."
                            ),
                        },
                        "frames": {
                            "type": "number",
                            "description": "Number of key frames to extract (default 4, max 8)",
                        },
                        "model": {
                            "type": "string",
                            "description": "Whisper model for transcription (default: base)",
                        },
                    },
                    "required": ["video_path"],
                },
            },
            {
                "name": "find_video_files",
                "description": (
                    "List video files under a directory path. Useful for batch processing."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "Directory to search (absolute path)",
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "Search subdirectories (default true)",
                        },
                    },
                    "required": ["directory"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "transcribe_video":
            return self._transcribe(
                tool_input["video_path"],
                model=tool_input.get("model", "base"),
            )

        if tool_name == "analyze_video":
            return self._analyze(
                tool_input["video_path"],
                prompt=tool_input.get("prompt"),
                frame_count=min(int(tool_input.get("frames", 4)), 8),
                model=tool_input.get("model", "base"),
            )

        if tool_name == "find_video_files":
            return self._find_videos(
                tool_input["directory"],
                recursive=tool_input.get("recursive", True),
            )

        return super().execute_tool(tool_name, tool_input)

    # ── Internal pipeline ─────────────────────────────────────────────────

    def _transcribe(self, video_path: str, model: str = "base") -> str:
        if not Path(video_path).exists():
            return json.dumps({"error": f"File not found: {video_path}"})

        with tempfile.TemporaryDirectory(prefix="media-agent-") as tmp:
            try:
                audio_path = self._extract_audio(video_path, tmp)
                transcript = self._run_whisper(audio_path, tmp, model)
                return json.dumps({
                    "transcript": transcript or "(no speech detected)",
                    "model": model,
                    "video": video_path,
                })
            except Exception as e:
                return json.dumps({"error": str(e), "video": video_path})

    def _analyze(
        self,
        video_path: str,
        prompt: str = None,
        frame_count: int = 4,
        model: str = "base",
    ) -> str:
        if not Path(video_path).exists():
            return json.dumps({"error": f"File not found: {video_path}"})

        with tempfile.TemporaryDirectory(prefix="media-agent-") as tmp:
            try:
                # Step 1: local transcript (free)
                try:
                    audio_path = self._extract_audio(video_path, tmp)
                    transcript = self._run_whisper(audio_path, tmp, model)
                except Exception as e:
                    transcript = f"(transcription failed: {e})"

                # Step 2: key frames (free)
                frame_paths = self._extract_frames(video_path, tmp, frame_count)

                # Step 3: Haiku analysis (minimal cost)
                analysis = self._haiku_analyze(frame_paths, transcript, prompt)

                return json.dumps({
                    "transcript": transcript or "(no speech detected)",
                    "analysis":   analysis,
                    "frames":     len(frame_paths),
                    "model":      model,
                    "video":      video_path,
                })
            except Exception as e:
                return json.dumps({"error": str(e), "video": video_path})

    def _find_videos(self, directory: str, recursive: bool = True) -> str:
        base = Path(directory)
        if not base.exists():
            return json.dumps({"error": f"Directory not found: {directory}"})

        pattern = "**/*" if recursive else "*"
        files = [
            str(p) for p in base.glob(pattern)
            if p.suffix.lower() in VIDEO_EXTENSIONS and p.is_file()
        ]
        return json.dumps({"files": sorted(files), "count": len(files)})

    # ── Low-level helpers ─────────────────────────────────────────────────

    def _extract_audio(self, video_path: str, tmp_dir: str) -> str:
        audio_path = os.path.join(tmp_dir, "audio.wav")
        subprocess.run(
            [FFMPEG, "-i", video_path, "-ar", "16000", "-ac", "1",
             "-vn", audio_path, "-y", "-loglevel", "error"],
            check=True, capture_output=True, timeout=120,
        )
        return audio_path

    def _run_whisper(self, audio_path: str, tmp_dir: str, model: str = "base") -> str:
        subprocess.run(
            [WHISPER, audio_path, "--model", model,
             "--output_format", "txt", "--output_dir", tmp_dir, "--fp16", "False"],
            check=True, capture_output=True, timeout=300, text=True,
        )
        txt_path = audio_path.replace(".wav", ".txt")
        return Path(txt_path).read_text(encoding="utf-8").strip() if Path(txt_path).exists() else ""

    def _extract_frames(self, video_path: str, tmp_dir: str, count: int = 4) -> list[str]:
        probe = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(probe.stdout.strip()) if probe.stdout.strip() else 10.0

        frames = []
        for i in range(count):
            ts   = (duration / (count + 1)) * (i + 1)
            fp   = os.path.join(tmp_dir, f"frame_{i}.jpg")
            subprocess.run(
                [FFMPEG, "-i", video_path, "-ss", f"{ts:.2f}",
                 "-vframes", "1", "-vf", "scale=640:-1", fp, "-y", "-loglevel", "error"],
                check=True, capture_output=True, timeout=30,
            )
            if Path(fp).exists():
                frames.append(fp)
        return frames

    def _haiku_analyze(
        self,
        frame_paths: list[str],
        transcript:  str,
        prompt:      str = None,
    ) -> str:
        user_prompt = prompt or (
            "Analyze this video. Based on the frames and transcript, provide: "
            "1) What is happening visually, 2) Key spoken content, "
            "3) Key learnings or takeaways."
        )
        content = [
            {
                "type": "image",
                "source": {
                    "type":       "base64",
                    "media_type": "image/jpeg",
                    "data":       base64.b64encode(Path(fp).read_bytes()).decode(),
                },
            }
            for fp in frame_paths
        ] + [{"type": "text", "text": f"Transcript:\n{transcript}\n\n{user_prompt}"}]

        response = self.client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 2048,
            messages   = [{"role": "user", "content": content}],
        )
        return response.content[0].text


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Media Agent")
    parser.add_argument("--task", type=str, required=True)
    args = parser.parse_args()

    agent  = MediaAgent()
    result = agent.run(args.task)
    print("=" * 60)
    if "error" in result:
        print(f"ERROR: {result['error']}")
    else:
        print(result["output"])
        print(f"\nIterations: {result.get('iterations')} | Tools: {len(result.get('tool_calls', []))}")
