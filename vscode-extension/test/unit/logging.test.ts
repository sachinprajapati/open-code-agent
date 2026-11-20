import { describe, expect, it } from "vitest";
import { redact } from "../../src/redaction";
describe("redaction", () => { it("redacts credential-shaped values", () => expect(redact("api_key=abc token: xyz")).not.toContain("abc")); });
