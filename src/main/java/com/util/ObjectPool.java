package com.util;

import java.util.ArrayDeque;
import java.util.function.Consumer;
import java.util.function.Supplier;

/**
 * Simple fixed-size object pool for reusable objects (e.g. VFX particles, decals).
 *
 * Usage:
 * <pre>
 *   ObjectPool<GPUParticles3D> pool = new ObjectPool<>(10, () -> createSplatter(), p -> p.setEmitting(false));
 *   GPUParticles3D p = pool.acquire();
 *   p.setGlobalPosition(hitPoint);
 *   p.setEmitting(true);
 *   // Release back when the particle finishes (e.g. via signal or timer)
 *   pool.release(p);
 * </pre>
 *
 * @param <T> the pooled type
 */
public class ObjectPool<T> {

    private final ArrayDeque<T> available;
    private final Consumer<T> resetFn;

    /**
     * @param capacity  maximum number of instances the pool holds
     * @param factory   creates a new instance when the pool is empty
     * @param resetFn   called on {@link #release} to return an object to a clean state
     */
    public ObjectPool(int capacity, Supplier<T> factory, Consumer<T> resetFn) {
        this.resetFn  = resetFn;
        this.available = new ArrayDeque<>(capacity);
        for (int i = 0; i < capacity; i++) {
            available.add(factory.get());
        }
    }

    /**
     * Acquire an object from the pool.
     * If the pool is exhausted, cycles back to the oldest borrowed object
     * (round-robin reuse — appropriate for fire-and-forget VFX).
     */
    public T acquire() {
        T obj = available.poll();
        if (obj == null) {
            throw new IllegalStateException("ObjectPool exhausted — increase capacity or release objects sooner");
        }
        return obj;
    }

    /**
     * Return an object to the pool. Calls the reset function before re-queuing.
     */
    public void release(T obj) {
        resetFn.accept(obj);
        available.offer(obj);
    }

    /** Number of objects currently available. */
    public int available() {
        return available.size();
    }
}
