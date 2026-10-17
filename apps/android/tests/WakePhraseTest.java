import ai.chintu.core.WakePhrase;

public final class WakePhraseTest {
    private static int assertions;

    private static void check(boolean condition, String message) {
        assertions++;
        if (!condition) throw new AssertionError(message);
    }

    public static void main(String[] args) {
        // What Vosk returned for "Chintu" in the measured voices.
        for (String heard : new String[]{"chin to", "[unk] chin to", "chin two", "[unk] chen to", "chin into",
                "chin to [unk] into", "[unk] chin two [unk]", "  CHIN   TOO  "}) {
            check(WakePhrase.matches(heard), "Should wake: " + heard);
        }
        // Decoys and ordinary speech the grammar maps elsewhere.
        for (String heard : new String[]{"", "[unk]", "pin to", "into", "chicken stew", "to stew", "chin", "to chin",
                "chin [unk] to", "kin to", "chinto", null}) {
            check(!WakePhrase.matches(heard), "Should not wake: " + heard);
        }
        check(WakePhrase.GRAMMAR.startsWith("[") && WakePhrase.GRAMMAR.endsWith("]") && WakePhrase.GRAMMAR.contains("\"[unk]\""),
                "Grammar is a JSON array ending with [unk]");
        System.out.println("WakePhraseTest: " + assertions + " assertions passed; Vosk wake phrases and decoys");
    }
}
