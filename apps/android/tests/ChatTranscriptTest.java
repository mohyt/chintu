import ai.chintu.core.ChatTranscript;
import java.util.List;

public final class ChatTranscriptTest {
    private static int assertions;

    private static void check(boolean condition, String message) {
        assertions++;
        if (!condition) throw new AssertionError(message);
    }

    private static String describe(List<ChatTranscript.Segment> segments) {
        StringBuilder text = new StringBuilder();
        for (ChatTranscript.Segment segment : segments) {
            text.append(segment.kind()).append(segment.open() ? "+" : "").append(':').append(segment.text()).append('|');
        }
        return text.toString();
    }

    public static void main(String[] args) {
        ChatTranscript live = new ChatTranscript();
        check(live.isEmpty(), "Starts empty");
        live.add(3, "progress", "Reading: notes.md");
        live.add(4, "output_delta", "The release ");
        live.add(5, "output_delta", "went out");
        check(describe(live.segments()).equals("STEP:Reading: notes.md|TEXT+:The release went out|"), "Streaming text stays open: " + describe(live.segments()));
        live.add(6, "output_delta", " on Tuesday.");
        live.add(7, "output", "The release went out on Tuesday.");
        live.add(8, "progress", "Running: git log");
        live.add(9, "output", "Checked the log too.");
        check(describe(live.segments()).equals("STEP:Reading: notes.md|TEXT:The release went out on Tuesday.|STEP:Running: git log|TEXT:Checked the log too.|"),
                "Finished output closes the block; plain output stands alone: " + describe(live.segments()));
        check(live.replyText().equals("The release went out on Tuesday.\n\nChecked the log too."), "Reply text joins text segments");

        // Duplicates and out-of-order backfill rebuild the same transcript.
        ChatTranscript backfill = new ChatTranscript();
        for (long id : new long[]{9, 8, 7, 6, 5, 4, 3, 7, 4}) {
            String kind = id == 3 || id == 8 ? "progress" : id == 7 || id == 9 ? "output" : "output_delta";
            String text = id == 3 ? "Reading: notes.md" : id == 4 ? "The release " : id == 5 ? "went out" : id == 6 ? " on Tuesday."
                    : id == 7 ? "The release went out on Tuesday." : id == 8 ? "Running: git log" : "Checked the log too.";
            backfill.add(id, kind, text);
        }
        check(describe(backfill.segments()).equals(describe(live.segments())), "Order and duplicates do not matter");
        check(!backfill.add(4, "output_delta", "again"), "A seen event is ignored");

        // A stream cut short by its budget is completed by the finished output; a tool call closes an open stream.
        ChatTranscript cut = new ChatTranscript();
        cut.add(1, "output_delta", "Partial");
        cut.add(2, "output", "Partial answer, complete.");
        cut.add(3, "output_delta", "Before a tool");
        cut.add(4, "progress", "  Searching: TODO  ");
        check(describe(cut.segments()).equals("TEXT:Partial answer, complete.|TEXT:Before a tool|STEP:Searching: TODO|"), describe(cut.segments()));

        ChatTranscript other = new ChatTranscript();
        check(!other.add(1, "completed", "done") && !other.add(2, "output", null) && other.isEmpty(), "Other kinds are ignored");
        other.add(3, "output", "   ");
        check(other.segments().isEmpty(), "Blank output adds nothing");
        System.out.println("ChatTranscriptTest: " + assertions + " assertions passed; streamed replies, steps, backfill and budgets");
    }
}
