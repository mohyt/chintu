package ai.chintu.core;

import java.text.Normalizer;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Parses the reserved Chintu command prefix without interpreting ordinary task text.
 * "Chintu, new chat [in FOLDER]" starts a fresh conversation (a new agent session), optionally in a named folder.
 */
public final class VoiceCommand {
    public enum Kind { TASK, SWITCH_DESTINATION, NEW_CONVERSATION, UNKNOWN_COMMAND }

    private static final Pattern NEW_CONVERSATION = Pattern.compile(
            "^(?:start (?:a )?)?new (?:chat|conversation|session)(?: (?:in|inside|at|for) (?:the |my )?(.+?))?$");

    private final Kind kind;
    private final String destinationName;
    private final String folderName;
    private final String utterance;

    private VoiceCommand(Kind kind, String destinationName, String folderName, String utterance) {
        this.kind = kind;
        this.destinationName = destinationName;
        this.folderName = folderName;
        this.utterance = utterance;
    }

    public Kind kind() { return kind; }
    public String destinationName() { return destinationName; }
    /** For NEW_CONVERSATION: the spoken folder, without a trailing "folder"/"directory"; null for the current one. */
    public String folderName() { return folderName; }
    public String utterance() { return utterance; }

    /** Case and punctuation normalization for exact command/name comparison only. */
    public static String normalize(String value) {
        if (value == null) return "";
        return Normalizer.normalize(value, Normalizer.Form.NFKC)
                .toLowerCase(Locale.ROOT)
                .replaceAll("[\\p{P}\\p{S}\\p{Z}\\s]+", " ")
                .trim();
    }

    public static VoiceCommand parse(String utterance) {
        String text = normalize(utterance);
        if (text.isEmpty()) throw new IllegalArgumentException("Empty voice instruction");
        if (!text.equals("chintu") && !text.startsWith("chintu ")) {
            return new VoiceCommand(Kind.TASK, null, null, utterance);
        }
        String body = text.equals("chintu") ? "" : text.substring("chintu ".length());
        if (body.equals("start over")) return new VoiceCommand(Kind.NEW_CONVERSATION, null, null, utterance);
        Matcher fresh = NEW_CONVERSATION.matcher(body);
        if (fresh.matches()) {
            String folder = fresh.group(1) == null ? null : fresh.group(1).replaceAll(" (?:folder|directory)$", "");
            if (folder != null && (folder.isEmpty() || folder.equals("folder") || folder.equals("directory"))) {
                return new VoiceCommand(Kind.UNKNOWN_COMMAND, null, null, utterance);
            }
            return new VoiceCommand(Kind.NEW_CONVERSATION, null, folder, utterance);
        }
        String target = null;
        // Longest prefix first, so optional "to" cannot become a missing target.
        if (body.startsWith("connect me to ")) {
            target = body.substring("connect me to ".length());
        } else if (!body.equals("connect me to") && body.startsWith("connect me ")) {
            target = body.substring("connect me ".length());
        } else if (body.startsWith("switch to ")) {
            target = body.substring("switch to ".length());
        }
        if (target == null || target.isEmpty()) {
            return new VoiceCommand(Kind.UNKNOWN_COMMAND, null, null, utterance);
        }
        return new VoiceCommand(Kind.SWITCH_DESTINATION, target, null, utterance);
    }
}
