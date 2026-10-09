"""High-speed particle spark simulation for Freerun piano visualizer."""

from __future__ import annotations
import numpy as np


class ParticleSystem:
    """Manages hit sparks and embers when notes strike piano keys."""

    def __init__(self, max_particles: int = 800, aspect_ratio: float = 16.0 / 9.0):
        self.max_particles = max_particles
        self.aspect_ratio = aspect_ratio
        self.count = 0

        # Parallel arrays for active particles
        self.pos = np.zeros((max_particles, 2), dtype=np.float32)
        self.vel = np.zeros((max_particles, 2), dtype=np.float32)
        self.color = np.zeros((max_particles, 4), dtype=np.float32)
        self.life = np.zeros(max_particles, dtype=np.float32)
        self.max_life = np.zeros(max_particles, dtype=np.float32)
        self.size = np.zeros((max_particles, 2), dtype=np.float32)

        self.free_indices = list(range(max_particles))
        self.active_mask = np.zeros(max_particles, dtype=bool)

        # Preallocated instance buffer array
        self.instance_buffer = np.zeros((max_particles, 8), dtype=np.float32)

    def spawn(
        self,
        center_x: float,
        y_hit: float,
        r: float,
        g: float,
        b: float,
        count: int = 2,
    ):
        """Spawns spark particles at the hit position."""
        if not self.free_indices:
            return

        for _ in range(count):
            if not self.free_indices:
                break
            idx = self.free_indices.pop()
            self.active_mask[idx] = True

            # Position slightly randomized around key center
            self.pos[idx, 0] = center_x + np.random.uniform(-0.012, 0.012)
            self.pos[idx, 1] = y_hit + np.random.uniform(0.005, 0.02)

            # Velocity: upward and slight lateral spread
            self.vel[idx, 0] = np.random.uniform(-0.04, 0.04)
            self.vel[idx, 1] = np.random.uniform(0.20, 0.55)

            # Lifetime between 0.15s and 0.40s
            dur = np.random.uniform(0.18, 0.38)
            self.life[idx] = dur
            self.max_life[idx] = dur

            # Color: bright spark
            self.color[idx, 0] = min(1.0, r * 1.25)
            self.color[idx, 1] = min(1.0, g * 1.25)
            self.color[idx, 2] = min(1.0, b * 1.25)
            self.color[idx, 3] = 1.0

            # Size in NDC
            sz_x = np.random.uniform(0.003, 0.006)
            sz_y = sz_x * self.aspect_ratio
            self.size[idx, 0] = sz_x
            self.size[idx, 1] = sz_y

    def update(self, dt: float) -> np.ndarray:
        """Updates particle physics and returns the instance buffer array."""
        if not np.any(self.active_mask):
            return self.instance_buffer[:0]

        # Gravity and integration
        gravity = 0.90
        idx = np.flatnonzero(self.active_mask)

        self.life[idx] -= dt
        dead = idx[self.life[idx] <= 0]
        for d in dead:
            self.active_mask[d] = False
            self.free_indices.append(int(d))

        alive = np.flatnonzero(self.active_mask)
        if len(alive) == 0:
            return self.instance_buffer[:0]

        self.pos[alive] += self.vel[alive] * dt
        self.vel[alive, 1] -= gravity * dt

        # Alpha decay
        alpha = np.clip(self.life[alive] / self.max_life[alive], 0.0, 1.0)
        self.color[alive, 3] = alpha

        # Pack into instance buffer: [x, y, sz_x, sz_y, r, g, b, a]
        n_alive = len(alive)
        self.instance_buffer[:n_alive, 0:2] = self.pos[alive]
        self.instance_buffer[:n_alive, 2:4] = self.size[alive]
        self.instance_buffer[:n_alive, 4:8] = self.color[alive]

        return self.instance_buffer[:n_alive]
