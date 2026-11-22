package ai.chintu.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Groups an Ataria board (docs/13-ataria.md) into the sections the phone shows.
 * Pure Java so it is tested without Android; the activity converts JSON into {@link Entry}.
 *
 * <p>A todo whose uid equals its parent item's uid is folded into that item's row (one tick covers
 * both). Other todos sit under their parent item when it is on the board, otherwise on their own.
 */
public final class AtariaBoard {
    public enum Section {
        NEEDS_YOU("Needs you"), TODAY("Today"), LATER("Later"), DIGEST("Digest"), DONE("Done");
        public final String title;
        Section(String title) { this.title = title; }
    }

    /** One board line, as the gateway sends it. Plain class rather than a record for older Android runtimes. */
    public static final class Entry {
        private final String uid, kind, priority, group, title, shortTitle, parent, lane, due;
        private final boolean done;

        public Entry(String uid, String kind, String priority, String group, String title, String shortTitle,
                     String parent, boolean done, String lane, String due) {
            this.uid = Objects.requireNonNull(uid); this.kind = Objects.requireNonNull(kind);
            this.priority = Objects.requireNonNull(priority); this.group = Objects.requireNonNull(group);
            this.title = Objects.requireNonNull(title); this.shortTitle = shortTitle; this.parent = parent;
            this.done = done; this.lane = lane; this.due = due;
        }
        public String uid() { return uid; }
        public String kind() { return kind; }
        public String priority() { return priority; }
        public String group() { return group; }
        public String title() { return title; }
        public String shortTitle() { return shortTitle; }
        public String parent() { return parent; }
        public boolean done() { return done; }
        public String lane() { return lane; }
        public String due() { return due; }
        public boolean isTodo() { return "todo".equals(kind); }
        /** The short title when there is one, else the title. */
        public String label() { return shortTitle == null || shortTitle.isBlank() ? title : shortTitle; }
    }

    /** One tickable line: an item (or a lone todo), the todo folded into it, and todos beneath it. */
    public static final class Row {
        private final Entry entry, action;
        private final List<Entry> children;
        Row(Entry entry, Entry action, List<Entry> children) { this.entry = entry; this.action = action; this.children = children; }
        public Entry entry() { return entry; }
        /** The todo folded into this item (same uid), or null. */
        public Entry action() { return action; }
        public List<Entry> children() { return children; }
    }

    public static final class Group {
        private final Section section;
        private final List<Row> rows;
        Group(Section section, List<Row> rows) { this.section = section; this.rows = rows; }
        public Section section() { return section; }
        public List<Row> rows() { return rows; }
    }

    private AtariaBoard() {}

    public static List<Group> sections(List<Entry> entries) {
        Map<String, Entry> items = new LinkedHashMap<>();
        for (Entry e : entries) if (!e.isTodo()) items.putIfAbsent(e.uid(), e);
        Map<String, Entry> folded = new LinkedHashMap<>();
        Map<String, List<Entry>> children = new LinkedHashMap<>();
        List<Entry> tops = new ArrayList<>(items.values());
        for (Entry e : entries) {
            if (!e.isTodo()) continue;
            Entry parent = e.parent() == null ? null : items.get(e.parent());
            if (parent != null && parent.uid().equals(e.uid())) folded.putIfAbsent(e.uid(), e);
            else if (parent != null) children.computeIfAbsent(parent.uid(), k -> new ArrayList<>()).add(e);
            else tops.add(e);
        }
        Map<Section, List<Row>> out = new LinkedHashMap<>();
        for (Section s : Section.values()) out.put(s, new ArrayList<>());
        for (Entry e : tops) {
            List<Entry> kids = new ArrayList<>(children.getOrDefault(e.uid(), List.of()));
            kids.sort(ORDER);
            Entry action = e.isTodo() ? null : folded.get(e.uid());
            out.get(section(e, action)).add(new Row(e, action, Collections.unmodifiableList(kids)));
        }
        List<Group> groups = new ArrayList<>();
        for (Map.Entry<Section, List<Row>> g : out.entrySet()) {
            if (g.getValue().isEmpty()) continue;
            g.getValue().sort(Comparator.comparing(Row::entry, ORDER));
            groups.add(new Group(g.getKey(), Collections.unmodifiableList(g.getValue())));
        }
        return Collections.unmodifiableList(groups);
    }

    static Section section(Entry e, Entry action) {
        boolean done = e.done() || "done".equals(e.group()) || (action != null && action.done());
        if (done) return Section.DONE;
        if ("info".equals(e.group())) return Section.DIGEST;
        return switch (e.priority()) {
            case "P0", "P1" -> Section.NEEDS_YOU;
            case "P2" -> Section.TODAY;
            default -> Section.LATER;
        };
    }

    static final Comparator<Entry> ORDER = Comparator
            .comparingInt((Entry e) -> rank(e.priority()))
            .thenComparing(e -> e.due() == null ? "9999-99-99" : e.due())
            .thenComparing(Entry::label, String.CASE_INSENSITIVE_ORDER);

    static int rank(String priority) {
        return switch (priority) { case "P0" -> 0; case "P1" -> 1; case "P2" -> 2; case "P3" -> 3; default -> 4; };
    }

    /** "P1 3 · P2 30 · open 49 · done today 9" from the board's counts, skipping zero priorities. */
    public static String countsLine(Map<String, Integer> counts) {
        List<String> parts = new ArrayList<>();
        for (String p : new String[]{"P0", "P1", "P2", "P3"}) {
            Integer n = counts.get(p);
            if (n != null && n > 0) parts.add(p + " " + n);
        }
        Integer open = counts.get("open"), done = counts.get("done_today");
        if (open != null) parts.add("open " + open);
        if (done != null && done > 0) parts.add("done today " + done);
        return String.join(" · ", parts);
    }
}
