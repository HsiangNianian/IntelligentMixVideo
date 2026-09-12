/** Build a self-contained TSX export and browser Player bundle inside the renderer sandbox. */
import fs from "node:fs/promises";
import ts from "typescript";
import prettier from "prettier";
import { build } from "esbuild";

/** Preserve source imports and declarations while replacing its default export with an optional-props wrapper. */
export async function exportTemplate(code, config, composition) {
  const source = ts.createSourceFile(
    "Template.tsx",
    code,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  let prefix = "IMVExport";
  while (code.includes(prefix)) prefix += "_";
  let component;
  const edits = [];
  for (const statement of source.statements) {
    if (ts.isExportAssignment(statement) && !statement.isExportEquals) {
      component = `${prefix}Source`;
      edits.push([
        statement.getStart(source),
        statement.end,
        `const ${component} = ${statement.expression.getText(source)};`,
      ]);
    } else if (
      statement.modifiers?.some(
        (mod) => mod.kind === ts.SyntaxKind.DefaultKeyword,
      )
    ) {
      if (
        !ts.isFunctionDeclaration(statement) &&
        !ts.isClassDeclaration(statement)
      )
        throw new Error("Default export must be a React component.");
      component = statement.name?.text ?? `${prefix}Source`;
      const modifiers = statement.modifiers.filter(
        (mod) =>
          ![ts.SyntaxKind.DefaultKeyword, ts.SyntaxKind.ExportKeyword].includes(
            mod.kind,
          ),
      );
      const updated = ts.isFunctionDeclaration(statement)
        ? ts.factory.updateFunctionDeclaration(
            statement,
            modifiers,
            statement.asteriskToken,
            ts.factory.createIdentifier(component),
            statement.typeParameters,
            statement.parameters,
            statement.type,
            statement.body,
          )
        : ts.factory.updateClassDeclaration(
            statement,
            modifiers,
            ts.factory.createIdentifier(component),
            statement.typeParameters,
            statement.heritageClauses,
            statement.members,
          );
      edits.push([
        statement.getStart(source),
        statement.end,
        ts.createPrinter().printNode(ts.EmitHint.Unspecified, updated, source),
      ]);
    }
  }
  if (!component || edits.length !== 1)
    throw new Error("Expected exactly one default component export.");
  let body = code;
  for (const [start, end, replacement] of edits.reverse())
    body = body.slice(0, start) + replacement + body.slice(end);
  return prettier.format(
    `/** Accepted typography template with editable default props; load Noto Sans CJK SC in the consuming composition. */
import * as ${prefix}React from 'react';
${body}
/** Composition dimensions and timing used during acceptance. */
export const ${prefix}Composition = ${JSON.stringify(composition)} as const;
/** Defaults reflect this accepted version; caller props can override them. */
const ${prefix}Defaults = ${JSON.stringify(config)} as const;
/** Render with accepted defaults when no props are supplied. */
export default function ${prefix}(props: Partial<${prefix}React.ComponentProps<typeof ${component}>> = {}) {
 return <${component} {...${prefix}Defaults} {...props} />;
}`,
    { parser: "typescript" },
  );
}

/** Bundle trusted Player hosting code plus accepted TSX into one sealed script, with no runtime compiler. */
export async function buildPresentation(root, config, composition) {
  const result = await build({
    entryPoints: ["/renderer/preview-host.tsx"],
    bundle: true,
    write: false,
    platform: "browser",
    format: "iife",
    target: "es2022",
    minify: true,
    jsx: "automatic",
    define: { "process.env.NODE_ENV": '"production"' },
    plugins: [
      {
        name: "accepted-template",
        /** Resolve host-owned virtual modules to the candidate and its scalar defaults. */
        setup(builder) {
          // Only these two host-owned virtual imports can resolve outside the renderer package.
          builder.onResolve({ filter: /^imv:template$/ }, () => ({
            path: `${root}/Template.tsx`,
          }));
          builder.onResolve({ filter: /^imv:config$/ }, () => ({
            path: "config",
            namespace: "imv",
          }));
          builder.onLoad({ filter: /.*/, namespace: "imv" }, () => ({
            contents: JSON.stringify({ config, composition }),
            loader: "json",
          }));
        },
      },
    ],
  });
  await fs.writeFile(`${root}/interactive.js`, result.outputFiles[0].text);
}
