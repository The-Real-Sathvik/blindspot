"""Single-passage feasibility benchmark with source quotation verification."""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from threading import Event, Thread
import time

from pydantic import BaseModel, Field

from app.models.research import EvidenceChunk
from app.services.ollama_client import InferenceMetrics, OllamaClient


class PassageClaim(BaseModel):
    research_finding: str = Field(min_length=1)
    claim_type: str = Field(min_length=1)
    supporting_quotation: str = Field(min_length=1)
    source_chunk_identifier: str = Field(min_length=1)
    uncertainty_or_missing_information: str = Field(min_length=1)


@dataclass(frozen=True)
class MemoryMeasurement:
    available_before_mb: float
    available_minimum_mb: float
    available_after_mb: float


def available_memory_mb() -> float:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    status = MemoryStatus(); status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("Could not inspect Windows memory.")
    return round(status.ullAvailPhys / (1024 * 1024), 1)


def benchmark_chunk(client: OllamaClient, chunk: EvidenceChunk) -> tuple[PassageClaim, InferenceMetrics, MemoryMeasurement, bool]:
    before = available_memory_mb(); minimum = before; stop = Event()
    def sample() -> None:
        nonlocal minimum
        while not stop.wait(0.25): minimum = min(minimum, available_memory_mb())
    sampler = Thread(target=sample, daemon=True); sampler.start()
    prompt = ("Use only the supplied passage. Return JSON matching the schema. Do not infer unstated facts. "
              f"Source chunk identifier: {chunk.id}\nPassage:\n{chunk.text}")
    try:
        claim, metrics = client.generate_structured(prompt, PassageClaim)
    finally:
        stop.set(); sampler.join(timeout=1)
    after = available_memory_mb()
    return claim, metrics, MemoryMeasurement(before, minimum, after), claim.supporting_quotation in chunk.text and claim.source_chunk_identifier == chunk.id
