package ai.chintu.core;

import java.util.Locale;

/**
 * The wake word as the on-device Vosk recogniser hears it.
 *
 * "chintu" is not in the small English models' vocabulary, so the grammar spells it with
 * in-vocabulary pieces ("chin to", "chin two", ...) and adds decoy words that soak up
 * near-rhymes such as "Pintu" or "chicken stew". Measured on synthetic voices with
 * vosk-model-small-en-in-0.4: 14/15 wake phrases caught, 0/15 false alarms; a confidence
 * threshold only lost wake phrases, so none is used.
 */
public final class WakePhrase {
    /** Vosk grammar (JSON array); "[unk]" absorbs everything else. */
    public static final String GRAMMAR = "[\"chin to\", \"chin too\", \"chin two\", \"chen to\", \"chin into\", "
            + "\"pin to\", \"pin too\", \"into\", \"in to\", \"chicken\", \"stew\", \"kin\", \"ten\", "
            + "\"to\", \"two\", \"too\", \"chin\", \"pin\", \"hey\", \"[unk]\"]";

    private WakePhrase() {}

    /** True when the recognised text holds "chin"/"chen" directly followed by to, too, two or into. */
    public static boolean matches(String text) {
        if (text == null) return false;
        String[] words = text.toLowerCase(Locale.ROOT).trim().split("\\s+");
        for (int i = 0; i + 1 < words.length; i++) {
            boolean first = words[i].equals("chin") || words[i].equals("chen");
            String next = words[i + 1];
            if (first && (next.equals("to") || next.equals("too") || next.equals("two") || next.equals("into"))) return true;
        }
        return false;
    }
}
