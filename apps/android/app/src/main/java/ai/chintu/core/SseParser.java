package ai.chintu.core;

/** Line-by-line server-sent-events parser: comments ignored, multi-line data joined, frames bounded. */
public final class SseParser {
    public interface Listener { void frame(String event, String data); }

    private final int limit;
    private String event = "";
    private final StringBuilder data = new StringBuilder();
    private boolean hasData;

    public SseParser(int limit) {
        if (limit < 1) throw new IllegalArgumentException("Limit must be positive");
        this.limit = limit;
    }

    /** Feed one line without its terminator; a blank line dispatches the pending frame. */
    public void line(String line, Listener listener) {
        if (line.isEmpty()) {
            if (hasData || !event.isEmpty()) listener.frame(event.isEmpty() ? "message" : event, data.toString());
            event = "";
            data.setLength(0);
            hasData = false;
            return;
        }
        if (line.startsWith(":")) return;
        int colon = line.indexOf(':');
        String field = colon < 0 ? line : line.substring(0, colon);
        String value = colon < 0 ? "" : line.substring(colon + 1);
        if (value.startsWith(" ")) value = value.substring(1);
        if (field.equals("event")) {
            event = value;
        } else if (field.equals("data")) {
            if (hasData) data.append('\n');
            data.append(value);
            hasData = true;
            if (data.length() > limit) throw new IllegalStateException("Event exceeds " + limit + " characters");
        }
        // id and retry: the envelope carries the ID, and the client owns its backoff.
    }
}
