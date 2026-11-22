package ai.chintu.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * One turn's visible output, rebuilt from its events in event-ID order: tool steps from {@code progress},
 * reply text from streamed {@code output_delta} pieces, closed by the finished {@code output}.
 * Events may arrive twice or out of order (live polling plus a history backfill); both are harmless.
 */
public final class ChatTranscript {
    public enum Kind { STEP, TEXT }

    public static final class Segment {
        private final Kind kind;
        private final String text;
        private final boolean open;

        Segment(Kind kind, String text, boolean open) {
            this.kind = kind;
            this.text = text;
            this.open = open;
        }

        public Kind kind() { return kind; }
        public String text() { return text; }
        /** True while reply text is still streaming in. */
        public boolean open() { return open; }
    }

    private static final class Entry {
        final String kind;
        final String text;
        Entry(String kind, String text) { this.kind = kind; this.text = text; }
    }

    private final TreeMap<Long, Entry> events = new TreeMap<>();

    /** Records one event; kinds other than progress, output and output_delta are ignored. */
    public synchronized boolean add(long eventId, String kind, String text) {
        if (text == null || !("progress".equals(kind) || "output".equals(kind) || "output_delta".equals(kind))) return false;
        if (events.containsKey(eventId)) return false;
        events.put(eventId, new Entry(kind, text));
        return true;
    }

    public synchronized boolean isEmpty() { return events.isEmpty(); }

    public synchronized List<Segment> segments() {
        List<Segment> result = new ArrayList<>();
        StringBuilder streaming = null;
        for (Map.Entry<Long, Entry> item : events.entrySet()) {
            Entry entry = item.getValue();
            switch (entry.kind) {
                case "output_delta":
                    if (streaming == null) streaming = new StringBuilder();
                    streaming.append(entry.text);
                    break;
                case "output":
                    // The finished message closes the streamed block; it wins only when the stream was cut short.
                    String finished = streaming == null || entry.text.length() > streaming.length()
                            ? entry.text : streaming.toString();
                    if (!finished.trim().isEmpty()) result.add(new Segment(Kind.TEXT, finished, false));
                    streaming = null;
                    break;
                default:
                    if (streaming != null && streaming.length() > 0) result.add(new Segment(Kind.TEXT, streaming.toString(), false));
                    streaming = null;
                    if (!entry.text.trim().isEmpty()) result.add(new Segment(Kind.STEP, entry.text.trim(), false));
            }
        }
        if (streaming != null && streaming.length() > 0) result.add(new Segment(Kind.TEXT, streaming.toString(), true));
        return Collections.unmodifiableList(result);
    }

    /** All reply text of the turn, for copying or reading aloud. */
    public synchronized String replyText() {
        StringBuilder text = new StringBuilder();
        for (Segment segment : segments()) {
            if (segment.kind() != Kind.TEXT) continue;
            if (text.length() > 0) text.append("\n\n");
            text.append(segment.text());
        }
        return text.toString();
    }
}
