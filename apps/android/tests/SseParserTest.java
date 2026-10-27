import ai.chintu.core.SseParser;
import java.util.ArrayList;
import java.util.List;

public final class SseParserTest {
    private static int assertions;

    private static void check(boolean condition, String message) {
        assertions++;
        if (!condition) throw new AssertionError(message);
    }

    private static List<String> feed(SseParser parser, String text) {
        List<String> frames = new ArrayList<>();
        for (String line : text.split("\n", -1)) parser.line(line, (event, data) -> frames.add(event + "=" + data));
        return frames;
    }

    public static void main(String[] args) {
        SseParser parser = new SseParser(100);
        List<String> frames = feed(parser, ": chintu event stream\nretry: 2000\n\nevent: ready\ndata: {\"cursor\":0}\n\n"
                + "id: 7\nevent: output\ndata: {\"a\":1}\n\n: keepalive\n\ndata: line one\ndata:line two\n\n");
        check(frames.size() == 3, "Comments and retry alone dispatch nothing: " + frames);
        check(frames.get(0).equals("ready={\"cursor\":0}"), frames.get(0));
        check(frames.get(1).equals("output={\"a\":1}"), frames.get(1));
        check(frames.get(2).equals("message=line one\nline two"), "Unnamed frames are messages; data lines join: " + frames.get(2));

        // A frame split across reads still arrives whole; state resets after each frame.
        SseParser split = new SseParser(100);
        List<String> pieces = new ArrayList<>();
        for (String line : new String[]{"event: output", "data: {\"x\":", ""}) split.line(line, (e, d) -> pieces.add(e + "=" + d));
        check(pieces.size() == 1 && pieces.get(0).equals("output={\"x\":"), "Frame dispatched on blank line: " + pieces);
        split.line("", (e, d) -> pieces.add("unexpected"));
        check(pieces.size() == 1, "Blank line without fields dispatches nothing");

        try {
            feed(new SseParser(10), "data: 0123456789ABC\n\n");
            throw new AssertionError("Oversized frame must fail");
        } catch (IllegalStateException expected) { assertions++; }
        System.out.println("SseParserTest: " + assertions + " assertions passed; frames, comments, multi-line data and limits");
    }
}
