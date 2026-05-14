"use client";

import { Button } from "@onyx-ai/opal/components";
import { SvgPlus } from "@onyx-ai/opal/icons";

export default function OpalDemoPage() {
  return (
    <div className="flex flex-col gap-4 p-8">
      <h1 className="text-text-01 text-2xl font-semibold">Opal smoke test</h1>
      <p className="text-text-03">
        If this paragraph reads with Opal token colors and the button below
        renders with the Opal design system, the integration is wired up.
      </p>
      <div>
        <Button
          variant="default"
          prominence="primary"
          icon={SvgPlus}
          onClick={() => {
            // eslint-disable-next-line no-console
            console.log("Opal Button clicked");
          }}
        >
          Create something
        </Button>
      </div>
    </div>
  );
}
