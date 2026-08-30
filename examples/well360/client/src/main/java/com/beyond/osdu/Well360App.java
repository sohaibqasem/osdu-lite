package com.beyond.osdu;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.opengroup.osdu.core.common.http.json.HttpResponseBodyMapper;
import org.opengroup.osdu.core.common.model.http.DpsHeaders;
import org.opengroup.osdu.core.common.model.search.CursorQueryRequest;
import org.opengroup.osdu.core.common.model.search.CursorQueryResponse;
import org.opengroup.osdu.core.common.model.search.QueryRequest;
import org.opengroup.osdu.core.common.model.search.QueryResponse;
import org.opengroup.osdu.core.common.search.ISearchService;
import org.opengroup.osdu.core.common.search.SearchAPIConfig;
import org.opengroup.osdu.core.common.search.SearchFactory;
import org.opengroup.osdu.core.common.storage.IStorageService;
import org.opengroup.osdu.core.common.storage.StorageAPIConfig;
import org.opengroup.osdu.core.common.storage.StorageFactory;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Well 360 POC aligned with the supplied OSDU tutorial.
 *
 * Search + Storage use the real org.opengroup.osdu:os-core-common:3.1.0 client.
 * Dataset retrieval uses HTTP because os-core-common 3.1.0 exposes Search/Storage
 * clients used by this POC, while Dataset is a separate OSDU service.
 */
public final class Well360App {
    private static final String BASE = env("OSDU_BASE_URL", "http://localhost:8089").replaceAll("/+$", "");
    private static final String PARTITION = env("OSDU_PARTITION", "company-prod");
    private static final String TOKEN = env("OSDU_TOKEN", "demo-token");

    private static final String WELL_KIND = "osdu:wks:master-data--Well:*";
    private static final String WELLBORE_KIND = "osdu:wks:master-data--Wellbore:*";
    private static final String WELLLOG_KIND = "osdu:wks:work-product-component--WellLog:*";

    private static final String WELL_ID = PARTITION + ":master-data--Well:W-1001";
    private static final String WELLBORE_ID = PARTITION + ":master-data--Wellbore:WB-1001-A";
    private static final String DATASET_ID = PARTITION + ":dataset--File.Generic:LOG-1001-GR-LAS";

    private static final ObjectMapper JSON = new ObjectMapper();

    private Well360App() {}

    public static void main(String[] args) throws Exception {
        System.out.println("============================================================");
        System.out.println("OSDU Well 360 Java POC");
        System.out.println("real client : org.opengroup.osdu:os-core-common:3.1.0");
        System.out.println("base        : " + BASE);
        System.out.println("partition   : " + PARTITION);
        System.out.println("============================================================");

        DpsHeaders headers = createHeaders();
        HttpResponseBodyMapper mapper = new HttpResponseBodyMapper(JSON);
        ISearchService search = createSearchService(headers, mapper);
        IStorageService storage = createStorageService(headers, mapper);

        Map<String, Object> well = searchWell(search);
        storageWell(storage);
        searchWellbores(search);
        searchWellLogs(search);
        retrieveLas();
        cursorDemo(search);

        System.out.println();
        System.out.println("============================================================");
        System.out.println("PASS - Well 360 flow completed.");
        System.out.println("Search -> Storage -> Wellbore -> WellLog -> Dataset -> LAS");
        System.out.println("EAGLE record: " + well.get("id"));
        System.out.println("============================================================");
    }

    private static DpsHeaders createHeaders() {
        Map<String, String> map = new LinkedHashMap<>();
        map.put(DpsHeaders.AUTHORIZATION, "Bearer " + TOKEN);
        map.put(DpsHeaders.DATA_PARTITION_ID, PARTITION);
        map.put(DpsHeaders.CORRELATION_ID, UUID.randomUUID().toString());
        map.put(DpsHeaders.CONTENT_TYPE, "application/json");
        DpsHeaders headers = DpsHeaders.createFromMap(map);
        headers.addCorrelationIdIfMissing();
        return headers;
    }

    private static ISearchService createSearchService(DpsHeaders headers, HttpResponseBodyMapper mapper) {
        SearchAPIConfig config = SearchAPIConfig.Default();
        config.setRootUrl(BASE + "/api/search/v2");
        return new SearchFactory(config, mapper).create(headers);
    }

    private static IStorageService createStorageService(DpsHeaders headers, HttpResponseBodyMapper mapper) {
        StorageAPIConfig config = StorageAPIConfig.Default();
        config.setRootUrl(BASE + "/api/storage/v2");
        return new StorageFactory(config, mapper).create(headers);
    }

    private static Map<String, Object> searchWell(ISearchService search) throws Exception {
        System.out.println();
        System.out.println("== 1. Search Well EAGLE-01 ==");

        QueryRequest request = new QueryRequest();
        request.setKind(WELL_KIND);
        request.setQuery("data.FacilityName:\"EAGLE-01\"");
        request.setLimit(20);
        request.setReturnedFields(List.of("id", "kind", "data.FacilityName", "tags"));

        QueryResponse response = search.search(request);
        if (response.getResults() == null || response.getResults().isEmpty()) {
            throw new IllegalStateException("EAGLE-01 was not found. Load the tutorial/full sample first.");
        }

        Map<String, Object> row = response.getResults().get(0);
        System.out.println(JSON.writerWithDefaultPrettyPrinter().writeValueAsString(row));
        return row;
    }

    private static void storageWell(IStorageService storage) throws Exception {
        System.out.println();
        System.out.println("== 2. Retrieve authoritative Well from Storage ==");
        var record = storage.getRecord(WELL_ID);
        System.out.println(record);
    }

    private static void searchWellbores(ISearchService search) throws Exception {
        System.out.println();
        System.out.println("== 3. Find Wellbores linked to EAGLE-01 ==");

        QueryRequest request = new QueryRequest();
        request.setKind(WELLBORE_KIND);
        request.setQuery("data.WellID:\"" + WELL_ID + "\"");
        request.setLimit(100);
        request.setReturnedFields(List.of("id", "data.FacilityName", "data.WellID"));

        QueryResponse response = search.search(request);
        System.out.println("wellbores = " + response.getTotalCount());
        for (Map<String, Object> row : response.getResults()) {
            System.out.println("  " + JSON.writeValueAsString(row));
        }
        if (response.getResults().isEmpty()) {
            throw new IllegalStateException("No Wellbores found for " + WELL_ID);
        }
    }

    private static void searchWellLogs(ISearchService search) throws Exception {
        System.out.println();
        System.out.println("== 4. Find WellLogs linked to EAGLE-01-A ==");

        QueryRequest request = new QueryRequest();
        request.setKind(WELLLOG_KIND);
        request.setQuery("data.WellboreID:\"" + WELLBORE_ID + "\"");
        request.setLimit(100);
        request.setReturnedFields(List.of("id", "data.Name", "data.WellboreID", "data.Datasets"));

        QueryResponse response = search.search(request);
        System.out.println("well logs = " + response.getTotalCount());
        for (Map<String, Object> row : response.getResults()) {
            System.out.println("  " + JSON.writeValueAsString(row));
        }
        if (response.getResults().isEmpty()) {
            throw new IllegalStateException("No WellLogs found for " + WELLBORE_ID);
        }
    }

    private static void retrieveLas() throws Exception {
        System.out.println();
        System.out.println("== 5. Dataset retrievalInstructions -> LAS download ==");

        HttpClient client = HttpClient.newHttpClient();
        String body = JSON.writeValueAsString(Map.of("datasetRegistryIds", List.of(DATASET_ID)));

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(BASE + "/api/dataset/v1/retrievalInstructions"))
                .header("Authorization", "Bearer " + TOKEN)
                .header("data-partition-id", PARTITION)
                .header("Content-Type", "application/json")
                .header("correlation-id", UUID.randomUUID().toString())
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();

        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() / 100 != 2) {
            throw new IllegalStateException("Dataset retrieval failed: HTTP " + response.statusCode() + " " + response.body());
        }

        Map<String, Object> parsed = JSON.readValue(response.body(), new TypeReference<>() {});
        List<Map<String, Object>> datasets = castListOfMaps(parsed.get("datasets"));
        if (datasets.isEmpty()) {
            throw new IllegalStateException("No retrieval instructions returned for " + DATASET_ID);
        }

        Map<String, Object> props = castMap(datasets.get(0).get("retrievalProperties"));
        String signedUrl = String.valueOf(props.get("signedUrl"));
        long declaredSize = props.containsKey("size") ? (long) Double.parseDouble(String.valueOf(props.get("size"))) : -1;
        String declaredSha256 = props.containsKey("sha256") ? String.valueOf(props.get("sha256")).toLowerCase() : null;
        System.out.println("signedUrl = " + signedUrl);
        System.out.println("size      = " + (declaredSize >= 0 ? declaredSize : "n/a"));
        System.out.println("sha256    = " + (declaredSha256 != null ? declaredSha256 : "n/a"));

        HttpResponse<byte[]> fileResponse = client.send(
                HttpRequest.newBuilder(URI.create(signedUrl)).GET().build(),
                HttpResponse.BodyHandlers.ofByteArray());

        if (fileResponse.statusCode() / 100 != 2) {
            throw new IllegalStateException("LAS download failed: HTTP " + fileResponse.statusCode());
        }

        Path dir = Path.of("downloads");
        Files.createDirectories(dir);
        Path target = dir.resolve("eagle01_gr_cali.las");
        Files.write(target, fileResponse.body());

        byte[] bytes = fileResponse.body();
        String computedSha256 = sha256(bytes);

        System.out.println("downloaded = " + target.toAbsolutePath());
        System.out.println("bytes local      = " + bytes.length);
        System.out.println("sha256 verified  = " + computedSha256);

        if (declaredSize >= 0 && declaredSize != bytes.length) {
            throw new IllegalStateException("Size mismatch: declared=" + declaredSize + " downloaded=" + bytes.length);
        }
        if (declaredSha256 != null && !declaredSha256.equals(computedSha256)) {
            throw new IllegalStateException("sha256 mismatch: declared=" + declaredSha256 + " computed=" + computedSha256);
        }
        if (declaredSize >= 0 || declaredSha256 != null) {
            System.out.println("file integrity verified against retrieval instructions");
        }
    }

    private static void cursorDemo(ISearchService search) throws Exception {
        System.out.println();
        System.out.println("== 6. Cursor search of all Wells (limit=1) ==");

        String cursor = null;
        int rows = 0;
        for (int page = 1; page <= 20; page++) {
            CursorQueryRequest request = new CursorQueryRequest();
            request.setKind(WELL_KIND);
            request.setLimit(1);
            request.setReturnedFields(List.of("id", "data.FacilityName"));
            if (cursor != null) request.setCursor(cursor);

            CursorQueryResponse response = search.searchCursor(request);
            int count = response.getResults() == null ? 0 : response.getResults().size();
            rows += count;
            System.out.printf("page=%d results=%d cursor=%s%n", page, count, response.getCursor());

            if (count == 0) break;
            // Deliberately do NOT stop because the cursor string stayed the same.
            cursor = response.getCursor();
        }
        System.out.println("cursor rows seen = " + rows);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castMap(Object value) {
        return value == null ? Map.of() : (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> castListOfMaps(Object value) {
        return value == null ? List.of() : (List<Map<String, Object>>) value;
    }

    private static String sha256(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? fallback : value;
    }
}
