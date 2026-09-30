/** Managed TypeScript language-service diagnostics; reads source and declarations without executing TSX. */
import ts from "typescript";
import path from "node:path";

/** Typecheck candidate and the actual default-props call site against installed declarations. */
export function languageDiagnostics(root, sprite = false) {
  const files = sprite
    ? [`${root}/Template.tsx`, `${root}/contract.tsx`]
    : [`${root}/Template.tsx`, `${root}/Export.tsx`, `${root}/contract.tsx`];
  const options = {
    strict: true,
    noEmit: true,
    skipLibCheck: true,
    jsx: ts.JsxEmit.ReactJSX,
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    esModuleInterop: true,
    types: ["react", "react-dom"],
  };
  // Language service checks the same real source/default-props call sites used for rendering.
  const service = ts.createLanguageService({
    getScriptFileNames: () => files,
    getScriptVersion: () => "1",
    getScriptSnapshot: (name) => {
      const source = ts.sys.readFile(name);
      return source === undefined
        ? undefined
        : ts.ScriptSnapshot.fromString(source);
    },
    getCurrentDirectory: () => root,
    getCompilationSettings: () => options,
    getDefaultLibFileName: ts.getDefaultLibFilePath,
    fileExists: ts.sys.fileExists,
    readFile: ts.sys.readFile,
    readDirectory: ts.sys.readDirectory,
    directoryExists: ts.sys.directoryExists,
    getDirectories: ts.sys.getDirectories,
  });
  let errors;
  let diagnostics;
  try {
    errors = ts.getPreEmitDiagnostics(service.getProgram());
    diagnostics = errors.map((diagnostic) => {
      const location =
        diagnostic.file && diagnostic.start !== undefined
          ? diagnostic.file.getLineAndCharacterOfPosition(diagnostic.start)
          : null;
      return {
        file: diagnostic.file ? path.basename(diagnostic.file.fileName) : null,
        code: diagnostic.code,
        line: location ? location.line + 1 : null,
        column: location ? location.character + 1 : null,
        severity: ts.DiagnosticCategory[diagnostic.category].toLowerCase(),
        message: ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n"),
      };
    });
  } finally {
    service.dispose();
  }
  return {
    diagnostics,
    detail: ts.formatDiagnostics(errors, {
      getCurrentDirectory: () => root,
      getCanonicalFileName: (name) => name,
      getNewLine: () => "\n",
    }),
  };
}
