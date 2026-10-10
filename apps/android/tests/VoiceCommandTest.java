import ai.chintu.core.DestinationMatcher;
import ai.chintu.core.VoiceCommand;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

public final class VoiceCommandTest {
    private static int assertions;

    private static void check(boolean condition, String message) {
        assertions++;
        if (!condition) throw new AssertionError(message);
    }

    private static void command(String text, VoiceCommand.Kind kind, String target) {
        VoiceCommand parsed = VoiceCommand.parse(text);
        check(parsed.kind() == kind, "Wrong command kind: " + text);
        check(target == null ? parsed.destinationName() == null : target.equals(parsed.destinationName()), "Wrong target: " + text);
        check(text.equals(parsed.utterance()), "Preserve original instruction: " + text);
    }

    private static void match(String name, List<DestinationMatcher.Destination> saved,
                              DestinationMatcher.Status status, String id) {
        DestinationMatcher.Match result = DestinationMatcher.match(name, saved);
        check(result.status() == status, "Wrong match status: " + name);
        check(id == null ? result.destination() == null : id.equals(result.destination().id()), "Wrong destination: " + name);
    }

    public static void main(String[] args) {
        command("Chintu, connect me cobra ubuntu", VoiceCommand.Kind.SWITCH_DESTINATION, "cobra ubuntu");
        command("Chintu connect me to Cobra Ubuntu", VoiceCommand.Kind.SWITCH_DESTINATION, "cobra ubuntu");
        command("Chintu switch to my mac", VoiceCommand.Kind.SWITCH_DESTINATION, "my mac");
        command("  CHINTU: connect me to COBRA-UBUNTU!  ", VoiceCommand.Kind.SWITCH_DESTINATION, "cobra ubuntu");
        command("Chintu\u00a0switch\tto\nMy Mac.", VoiceCommand.Kind.SWITCH_DESTINATION, "my mac");
        command("ＣＨＩＮＴＵ， connect me to Cobra Ubuntu", VoiceCommand.Kind.SWITCH_DESTINATION, "cobra ubuntu");
        for (String text : Arrays.asList("Chintu", "Chintu!", "Chintu connect me", "Chintu connect me to",
                "Chintu switch to", "Chintu run the tests", "Chintu please connect me to mac", "Chintu stop listening")) {
            command(text, VoiceCommand.Kind.UNKNOWN_COMMAND, null);
        }
        // Keyword inside ordinary speech and homonyms do not trigger routing.
        for (String text : Arrays.asList("Explain Chintu's architecture.", "Connect me to cobra ubuntu",
                "Chintoo connect me to cobra ubuntu", "Chintuish switch to mac", "Hey Chintu switch to mac")) {
            command(text, VoiceCommand.Kind.TASK, null);
        }
        for (String text : new String[]{null, "", "  !!! "}) {
            try { VoiceCommand.parse(text); throw new AssertionError("Empty speech must be rejected"); }
            catch (IllegalArgumentException expected) { assertions++; }
        }

        DestinationMatcher.Destination cobra = new DestinationMatcher.Destination("ubuntu", "Cobra Ubuntu", " cobra, linux desktop, , COBRA-UBUNTU ");
        DestinationMatcher.Destination mac = new DestinationMatcher.Destination("mac", "Mac Studio", "Mac, apple desktop");
        List<DestinationMatcher.Destination> saved = Arrays.asList(cobra, mac);
        match("COBRA—UBUNTU!", saved, DestinationMatcher.Status.MATCHED, "ubuntu");
        match("linux desktop", saved, DestinationMatcher.Status.MATCHED, "ubuntu");
        match("my mac", saved, DestinationMatcher.Status.MATCHED, "mac");
        match("apple desktop", saved, DestinationMatcher.Status.MATCHED, "mac");
        match("mac", saved, DestinationMatcher.Status.MATCHED, "mac");
        for (String name : new String[]{"cobra ubunto", "cobra ubun two", "mack", "apple", "mac studio extra", "", null}) {
            match(name, saved, DestinationMatcher.Status.NOT_FOUND, null);
        }
        DestinationMatcher.Destination other = new DestinationMatcher.Destination("other", "Other Host", "cobra ubuntu, mac");
        match("cobra ubuntu", Arrays.asList(cobra, other), DestinationMatcher.Status.AMBIGUOUS, null);
        match("my mac", Arrays.asList(mac, other), DestinationMatcher.Status.AMBIGUOUS, null);
        match("cobra ubuntu", Arrays.asList(cobra, cobra), DestinationMatcher.Status.AMBIGUOUS, null);

        DestinationMatcher.Destination literalMyMac = new DestinationMatcher.Destination("literal", "Travel Host", "my mac");
        match("my mac", Arrays.asList(mac, literalMyMac), DestinationMatcher.Status.MATCHED, "literal");
        DestinationMatcher.Destination literalCollision = new DestinationMatcher.Destination("collision", "MY MAC", "");
        match("my mac", Arrays.asList(mac, literalMyMac, literalCollision), DestinationMatcher.Status.AMBIGUOUS, null);
        match("cobra ubuntu", Collections.emptyList(), DestinationMatcher.Status.NOT_FOUND, null);

        // Never let the Android device locale change reserved keyword/name casing.
        Locale original = Locale.getDefault();
        try {
            Locale.setDefault(Locale.forLanguageTag("tr-TR"));
            command("CHINTU CONNECT ME TO LINUX DESKTOP", VoiceCommand.Kind.SWITCH_DESTINATION, "linux desktop");
            match("LINUX DESKTOP", saved, DestinationMatcher.Status.MATCHED, "ubuntu");
        } finally {
            Locale.setDefault(original);
        }
        System.out.println("VoiceCommandTest: " + assertions + " assertions passed; exact commands, aliases, ambiguity, homonyms and ordinary tasks");
    }
}
