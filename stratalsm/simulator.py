"""Workload generator and simulator for live demonstration and testing."""
import random
import string
from typing import Dict, Any, List
from stratalsm.engine import StrataLSM


class WorkloadSimulator:
    WORKLOADS = ["write_heavy", "read_heavy", "compaction_storm", "churn_delete", "balanced"]

    def __init__(self, engine: StrataLSM):
        self.engine = engine
        self.known_keys: List[str] = [f"key_{i:04d}" for i in range(100)]
        # Seed initial keys
        for k in self.known_keys[:30]:
            self.engine.put(k, f"val_init_{k}")

    def _rand_str(self, length: int = 16) -> str:
        return "".join(random.choices(string.ascii_letters + string.digits, k=length))

    def step(self, workload: str = "balanced", count: int = 25) -> Dict[str, Any]:
        if workload not in self.WORKLOADS:
            workload = "balanced"

        for _ in range(count):
            if workload == "write_heavy":
                k = f"key_{random.randint(0, 500):04d}"
                v = self._rand_str(64)
                self.engine.put(k, v)
                if k not in self.known_keys:
                    self.known_keys.append(k)

            elif workload == "read_heavy":
                if random.random() < 0.8 and self.known_keys:
                    k = random.choice(self.known_keys)
                else:
                    k = f"nonexistent_{random.randint(0, 9999)}"
                self.engine.get(k)

            elif workload == "compaction_storm":
                # Overwrite a narrow set of keys rapidly to trigger lots of SSTables and compactions
                k = f"hotspot_{random.randint(0, 15):02d}"
                v = f"storm_data_{self._rand_str(128)}"
                self.engine.put(k, v)

            elif workload == "churn_delete":
                if random.random() < 0.6 and self.known_keys:
                    k = random.choice(self.known_keys)
                    self.engine.delete(k)
                else:
                    k = f"key_{random.randint(0, 200):04d}"
                    v = self._rand_str(32)
                    self.engine.put(k, v)
                    if k not in self.known_keys:
                        self.known_keys.append(k)

            else:  # balanced
                roll = random.random()
                if roll < 0.5:
                    k = f"key_{random.randint(0, 200):04d}"
                    v = self._rand_str(48)
                    self.engine.put(k, v)
                    if k not in self.known_keys:
                        self.known_keys.append(k)
                elif roll < 0.85:
                    k = random.choice(self.known_keys) if (self.known_keys and random.random() < 0.7) else f"miss_{random.randint(0, 1000)}"
                    self.engine.get(k)
                else:
                    if self.known_keys:
                        k = random.choice(self.known_keys)
                        self.engine.delete(k)

        return self.engine.get_telemetry()
