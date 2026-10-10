import ai.chintu.core.EventCursor;
import ai.chintu.core.SubmissionGuard;
import java.util.concurrent.atomic.AtomicInteger;

public final class CoreTest {
    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    static final class Store implements SubmissionGuard.Store {
        String value;
        String accepted;
        boolean failAcceptance;
        public String read() { return value; }
        public void write(String request) { value = request; }
        public void clear() { value = null; }
        public void accept(String id) {
            if (failAcceptance) throw new IllegalStateException("acceptance disk failure");
            accepted = id; value = null;
        }
        public String acceptedTaskId() { return accepted; }
    }
    public static void main(String[] args) {
        Store disk = new Store();
        AtomicInteger creations = new AtomicInteger();
        SubmissionGuard guard = new SubmissionGuard(disk);
        String first = guard.prepare(key -> { creations.incrementAndGet(); return key + ":original"; });
        // Lost HTTP response, UI content changed, then process restart: replay original bytes.
        String retried = new SubmissionGuard(disk).prepare(key -> key + ":edited");
        check(first.equals(retried), "Retry after restart must preserve payload and key");
        check(creations.get() == 1, "Only one intentional submission created");
        String taskId = "11111111-1111-4111-8111-111111111111";
        disk.failAcceptance = true;
        try { guard.accepted(taskId); throw new AssertionError("Failed acceptance must throw"); }
        catch (IllegalStateException expected) {}
        SubmissionGuard restartedSubmission = new SubmissionGuard(disk);
        check(first.equals(restartedSubmission.pending()), "Failed acceptance must preserve exact pending request");
        check(restartedSubmission.acceptedTaskId() == null, "Failed acceptance must not expose a committed identity");
        try { guard.accepted("not-a-uuid"); throw new AssertionError("Invalid UUID accepted"); }
        catch (IllegalArgumentException expected) {}
        check(first.equals(guard.pending()), "Malformed acceptance must preserve pending request");
        disk.failAcceptance = false;
        guard.accepted(taskId);
        SubmissionGuard afterAcceptance = new SubmissionGuard(disk);
        check(afterAcceptance.pending() == null, "Accepted transaction clears pending");
        check(taskId.equals(afterAcceptance.acceptedTaskId()), "Accepted identity survives restart before UI callback");
        String second = guard.prepare(key -> key + ":original");
        check(!first.equals(second), "New intentional submission needs a new key");
        SubmissionGuard broken = new SubmissionGuard(new SubmissionGuard.Store() {
            public String read() { return null; }
            public void write(String value) { throw new IllegalStateException("disk full"); }
            public void clear() {}
            public void accept(String id) {}
            public String acceptedTaskId() { return null; }
        });
        try { broken.prepare(key -> key); throw new AssertionError("Must not send on disk failure"); }
        catch (IllegalStateException expected) {}
        EventCursor events = new EventCursor(7);
        check(!events.accept(7), "Duplicates ignored");
        check(events.accept(9), "Global cursor gaps allowed");
        check(!events.canSpeak(), "Reconnect backlog is silent");
        events.finishPage(9, true);
        check(!events.canSpeak(), "All backlog pages must stay silent");
        events.finishPage(9, false);
        check(events.canSpeak(), "Live page can speak after catch-up");
        check(events.accept(10), "Live event consumed");
        EventCursor restarted = new EventCursor(events.get());
        check(!restarted.accept(10), "Persisted event must not repeat speech after crash");
        events.reconnect();
        check(!events.canSpeak(), "Background reconnect must suppress old speech");
        events.resync(20);
        check(events.get() == 20 && !events.canSpeak(), "Expired cursor resets silently");
        try { events.finishPage(19, false); throw new AssertionError("Reject regressed cursor"); }
        catch (IllegalArgumentException expected) {}
        System.out.println("CoreTest: submission restart/retry, failed submission/acceptance persistence, accepted identity recovery, event replay/resync passed");
    }
}
