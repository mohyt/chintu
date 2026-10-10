package ai.chintu.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Exact saved-name/alias lookup. No partial, homonym or fuzzy destination selection. */
public final class DestinationMatcher {
    private DestinationMatcher() {}

    public enum Status { MATCHED, NOT_FOUND, AMBIGUOUS }

    public static final class Destination {
        private final String id;
        private final String friendlyName;
        private final List<String> names;

        public Destination(String id, String friendlyName, String commaSeparatedAliases) {
            if (id == null || id.trim().isEmpty()) throw new IllegalArgumentException("Missing destination ID");
            if (VoiceCommand.normalize(friendlyName).isEmpty()) throw new IllegalArgumentException("Missing destination name");
            this.id = id;
            this.friendlyName = friendlyName;
            List<String> normalized = new ArrayList<>();
            normalized.add(VoiceCommand.normalize(friendlyName));
            if (commaSeparatedAliases != null) {
                for (String alias : commaSeparatedAliases.split(",")) {
                    String name = VoiceCommand.normalize(alias);
                    if (!name.isEmpty() && !normalized.contains(name)) normalized.add(name);
                }
            }
            this.names = Collections.unmodifiableList(normalized);
        }

        public String id() { return id; }
        public String friendlyName() { return friendlyName; }
        private boolean matches(String name) { return names.contains(name); }
    }

    public static final class Match {
        private final Status status;
        private final Destination destination;

        private Match(Status status, Destination destination) {
            this.status = status;
            this.destination = destination;
        }

        public Status status() { return status; }
        /** Only MATCHED provides a destination. Callers retain selection otherwise. */
        public Destination destination() { return destination; }
    }

    public static Match match(String spokenName, List<Destination> destinations) {
        if (destinations == null) throw new IllegalArgumentException("Missing destination list");
        String name = VoiceCommand.normalize(spokenName);
        if (name.isEmpty()) return new Match(Status.NOT_FOUND, null);
        Match exact = exact(name, destinations);
        if (exact.status != Status.NOT_FOUND) return exact;
        // Literal saved "my ..." names/aliases take precedence over removing "my".
        if (name.startsWith("my ")) return exact(name.substring(3), destinations);
        return exact;
    }

    private static Match exact(String name, List<Destination> destinations) {
        Destination found = null;
        for (Destination destination : destinations) {
            if (destination == null) throw new IllegalArgumentException("Missing destination entry");
            if (destination.matches(name)) {
                if (found != null) return new Match(Status.AMBIGUOUS, null);
                found = destination;
            }
        }
        return found == null ? new Match(Status.NOT_FOUND, null) : new Match(Status.MATCHED, found);
    }
}
