package ai.chintu.core;

/** New foreground/reconnect cycles consume backlog silently before allowing live speech. */
public final class EventCursor {
    private long cursor;
    private boolean catchingUp = true;
    public EventCursor(long persistedCursor) {
        if (persistedCursor < 0) throw new IllegalArgumentException("Negative cursor");
        cursor = persistedCursor;
    }
    public long get() { return cursor; }
    public boolean accept(long id) {
        if (id <= cursor) return false;
        cursor = id;
        return true;
    }
    public boolean canSpeak() { return !catchingUp; }
    public void finishPage(long nextCursor, boolean hasMore) {
        if (nextCursor < cursor) throw new IllegalArgumentException("Cursor regressed");
        cursor = nextCursor;
        if (!hasMore) catchingUp = false;
    }
    public void reconnect() { catchingUp = true; }
    public void resync(long resyncCursor) {
        if (resyncCursor < 0) throw new IllegalArgumentException("Negative cursor");
        cursor = resyncCursor;
        catchingUp = true;
    }
}
