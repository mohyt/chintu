import ai.chintu.core.AtariaBoard;
import ai.chintu.core.AtariaBoard.Entry;
import ai.chintu.core.AtariaBoard.Group;
import java.util.List;
import java.util.Map;

public final class AtariaBoardTest {
    private static int assertions;

    private static void check(boolean condition, String message) {
        assertions++;
        if (!condition) throw new AssertionError(message);
    }

    private static Entry item(String uid, String priority, String group, boolean done, String due) {
        return new Entry(uid, "item", priority, group, "Item " + uid, null, null, done, uid.split("\\.")[0], due);
    }

    private static Entry todo(String uid, String parent, String priority, boolean done) {
        return new Entry(uid, "todo", priority, done ? "done" : "open", "Todo " + uid, "Do " + uid, parent, done, uid.split("\\.")[0], null);
    }

    private static String describe(List<Group> groups) {
        StringBuilder out = new StringBuilder();
        for (Group g : groups) {
            out.append(g.section().name()).append('[');
            for (AtariaBoard.Row r : g.rows()) {
                out.append(r.entry().uid());
                if (r.action() != null) out.append("+action");
                if (!r.children().isEmpty()) {
                    out.append('(');
                    for (Entry c : r.children()) out.append(c.uid()).append(' ');
                    out.setLength(out.length() - 1);
                    out.append(')');
                }
                out.append(' ');
            }
            if (!g.rows().isEmpty()) out.setLength(out.length() - 1);
            out.append(']');
        }
        return out.toString();
    }

    public static void main(String[] args) {
        List<Entry> board = List.of(
                item("work.b", "P2", "open", false, "2026-11-23"),
                item("work.a", "P2", "open", false, "2026-11-22"),
                item("meetings.m", "P1", "open", false, null),
                todo("meetings.m", "meetings.m", "P1", false),       // same uid as its item: folded in
                todo("work.t1", "work.b", "P2", false),              // under its item
                todo("work.t0", "work.b", "P1", false),
                todo("work.lone", "work.gone", "P0", false),         // parent not on the board: stands alone
                item("personal.news", "P3", "info", false, null),
                item("work.closed", "P1", "done", true, null),
                item("finance.f", "P3", "open", false, null),
                item("ledger.o", "OK", "open", false, null));
        String got = describe(AtariaBoard.sections(board));
        check(got.equals("NEEDS_YOU[work.lone meetings.m+action]TODAY[work.a work.b(work.t0 work.t1)]LATER[finance.f ledger.o]"
                + "DIGEST[personal.news]DONE[work.closed]"), "Sections, folding and order: " + got);

        // A folded todo that is done moves its item to Done.
        List<Entry> ticked = List.of(item("work.x", "P1", "open", false, null), todo("work.x", "work.x", "P1", true));
        check(describe(AtariaBoard.sections(ticked)).equals("DONE[work.x+action]"), "Folded done todo: " + describe(AtariaBoard.sections(ticked)));

        // Empty sections are left out; an empty board has no groups.
        check(AtariaBoard.sections(List.of()).isEmpty(), "Empty board");

        check(AtariaBoard.countsLine(Map.of("P0", 0, "P1", 3, "P2", 30, "open", 49, "done_today", 9))
                .equals("P1 3 · P2 30 · open 49 · done today 9"), "Counts line");
        check(AtariaBoard.countsLine(Map.of("open", 0)).equals("open 0"), "Counts line with nothing open");

        check(todo("work.q", null, "P2", false).label().equals("Do work.q"), "Short title wins");
        check(item("work.q", "P2", "open", false, null).label().equals("Item work.q"), "Title when no short title");
        System.out.println("PASS: Ataria board grouping, " + assertions + " assertions");
    }
}
