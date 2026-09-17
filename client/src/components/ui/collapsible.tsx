/** shadcn/ui 折叠控件：复用 Radix 的展开状态与可访问关联。
 * Copyright (c) 2023 shadcn — MIT；完整声明见 client/public/THIRD_PARTY_NOTICES.txt。
 */

import { Collapsible as CollapsiblePrimitive } from "radix-ui";

/** 控制一个可展开区域。 */
function Collapsible({
  ...props
}: React.ComponentProps<typeof CollapsiblePrimitive.Root>) {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />;
}

/** 为触发按钮关联 aria-expanded 和内容 ID。 */
function CollapsibleTrigger({
  ...props
}: React.ComponentProps<typeof CollapsiblePrimitive.CollapsibleTrigger>) {
  return (
    <CollapsiblePrimitive.CollapsibleTrigger
      data-slot="collapsible-trigger"
      {...props}
    />
  );
}

/** 仅在展开时显示对应内容。 */
function CollapsibleContent({
  ...props
}: React.ComponentProps<typeof CollapsiblePrimitive.CollapsibleContent>) {
  return (
    <CollapsiblePrimitive.CollapsibleContent
      data-slot="collapsible-content"
      {...props}
    />
  );
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent };
