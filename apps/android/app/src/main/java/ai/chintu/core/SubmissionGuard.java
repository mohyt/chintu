package ai.chintu.core;

import java.util.UUID;

/** Persists an exact request before network I/O. Never changes identity after an uncertain result. */
public final class SubmissionGuard {
    public interface Store {
        String read();
        void write(String request);
        void clear();
        /** Atomically records task identity and removes pending request, or throws without mutation. */
        void accept(String taskId);
        String acceptedTaskId();
    }
    public interface Factory { String create(String submissionKey); }
    private final Store store;
    public SubmissionGuard(Store store) { this.store = store; }
    public synchronized String prepare(Factory factory) {
        String pending = store.read();
        if (pending != null) return pending;
        String request = factory.create(UUID.randomUUID().toString());
        store.write(request); // A failed write must fail submission, not send an unpersisted request.
        return request;
    }
    public synchronized String pending() { return store.read(); }
    public synchronized void accepted(String taskId) {
        if (taskId == null || !UUID.fromString(taskId).toString().equalsIgnoreCase(taskId))
            throw new IllegalArgumentException("Invalid accepted task UUID");
        store.accept(taskId);
    }
    public synchronized String acceptedTaskId() { return store.acceptedTaskId(); }
    // Rejection is deliberately explicit; transport errors never call this.
    public synchronized void rejected() { store.clear(); }
}
