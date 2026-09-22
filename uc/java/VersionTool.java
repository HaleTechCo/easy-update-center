import hudson.util.VersionNumber;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Batch Jenkins version comparisons using the generator's own hudson.util.VersionNumber.
 * Reads tab-separated lines on stdin. Modes: compare (a TAB b -> -1|0|1), sort (v -> ascending),
 * max (key TAB v -> key TAB newest).
 */
public class VersionTool {
    private static int cmp(String a, String b) {
        return Integer.signum(new VersionNumber(a).compareTo(new VersionNumber(b)));
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            System.err.println("usage: VersionTool compare|sort|max");
            System.exit(64);
        }
        List<String> lines = new ArrayList<>();
        try (BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8))) {
            for (String line; (line = in.readLine()) != null; ) {
                if (!line.isEmpty()) {
                    lines.add(line);
                }
            }
        }
        StringBuilder out = new StringBuilder();
        switch (args[0]) {
            case "compare" -> {
                for (String line : lines) {
                    String[] p = line.split("\t", 2);
                    out.append(cmp(p[0], p[1])).append('\n');
                }
            }
            case "sort" -> lines.stream().sorted(VersionTool::cmp).forEach(v -> out.append(v).append('\n'));
            case "max" -> {
                Map<String, String> best = new LinkedHashMap<>();
                for (String line : lines) {
                    String[] p = line.split("\t", 2);
                    best.merge(p[0], p[1], (a, b) -> cmp(a, b) >= 0 ? a : b);
                }
                best.forEach((k, v) -> out.append(k).append('\t').append(v).append('\n'));
            }
            default -> {
                System.err.println("unknown mode: " + args[0]);
                System.exit(64);
            }
        }
        System.out.print(out);
    }
}
