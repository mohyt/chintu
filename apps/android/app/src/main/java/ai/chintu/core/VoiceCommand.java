package ai.chintu.core;

import java.text.Normalizer;
import java.util.Locale;

/** Parses the reserved Chintu command prefix without interpreting ordinary task text. */
public final class VoiceCommand {
    public enum Kind { TASK, SWITCH_DESTINATION, UNKNOWN_COMMAND }

    private final Kind kind;
    private final String destinationName;
    private final String utterance;

    private VoiceCommand(Kind kind, String destinationName, String utterance) {
        this.kind = kind;
        this.destinationName = destinationName;
        this.utterance = utterance;
    }

    public Kind kind() { return kind; }
    public String destinationName() { return destinationName; }
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
            return new VoiceCommand(Kind.TASK, null, utterance);
        }
        String body = text.equals("chintu") ? "" : text.substring("chintu ".length());
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
            return new VoiceCommand(Kind.UNKNOWN_COMMAND, null, utterance);
        }
        return new VoiceCommand(Kind.SWITCH_DESTINATION, target, utterance);
    }
}
