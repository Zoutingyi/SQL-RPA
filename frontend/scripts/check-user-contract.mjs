import fs from "node:fs";

const snapshot = JSON.parse(fs.readFileSync("../contracts/user-api-v1.json", "utf8"));
const generated = fs.readFileSync("src/api/user-api-v1.generated.ts", "utf8");
const match = generated.match(/USER_API_V1_SNAPSHOT = ([\s\S]+) as const;\r?\nexport type ApiErrorCode/);
const generatedSnapshot = match ? JSON.parse(match[1]) : null;
if (!generatedSnapshot || JSON.stringify(generatedSnapshot) !== JSON.stringify(snapshot)) {
  throw new Error("OpenAPI/TypeScript/error-code snapshot drift detected");
}
if (snapshot.contract_version !== "user-api-v1") {
  throw new Error(`Unexpected user API contract version: ${snapshot.contract_version}`);
}
console.log("user-api-v1 contract snapshot is consistent");
