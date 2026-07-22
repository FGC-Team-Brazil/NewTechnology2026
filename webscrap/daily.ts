import { DOMParser } from "linkedom";
import fs from "fs/promises";
import { join } from "path";
import { zip } from "zip-a-folder";
import { ArchiveInput } from "bun";

const BASE_URL =
        "https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/diario/America_Sul/";
const OUTPUT_DIR = "./focos_diarios";

if (!(await fs.exists(OUTPUT_DIR))) {
        await fs.mkdir(OUTPUT_DIR, { recursive: true });
}

async function get_recent_files(daysAgo = 60) {
        console.log(`Fetching INPE server: ${BASE_URL}`);

        try {
                const response = await fetch(BASE_URL);
                if (!response.ok) throw new Error(`Status: ${response.status}`);

                const html = await response.text();

                // Parsing HTML
                const document = new DOMParser().parseFromString(
                        html,
                        "text/html",
                );
                const links = document.querySelectorAll("a");

                // Define the date limit (2 months)
                const limitDate = new Date();
                limitDate.setDate(limitDate.getDate() - daysAgo);

                let filesToDownload: string[] = [];

                for (const link of links) {
                        const href = link.getAttribute("href") || "";

                        // Validates if the link matches the 10 minutes files pattern
                        if (
                                href.startsWith("focos_diario") &&
                                href.endsWith(".csv")
                        ) {
                                // Extract the files from the pattern: "focos_10min_20260702_0000.csv" -> "20260702"
                                const match = href.match(/(\d{8})/);
                                if (match) {
                                        const dateStr = match[1];
                                        const year = dateStr.substring(0, 4);
                                        const month = dateStr.substring(4, 6);
                                        const day = dateStr.substring(6, 8);

                                        const file_date = new Date(
                                                `${year}-${month}-${day}T00:00:00`,
                                        );

                                        // Checks if the file is between the time window
                                        if (file_date >= limitDate) {
                                                filesToDownload.push(href);
                                        }
                                }
                        }
                }

                console.log(
                        `Found ${filesToDownload.length} valid ${filesToDownload.length > 1 ? "files" : "file"} on the last ${daysAgo} days.`,
                );

                // executes the filtered files sequential download
                for (const fileName of filesToDownload) {
                        const saveFile = Bun.file(join(OUTPUT_DIR, fileName));

                        // Skips if the file already exists to save resources
                        if (await saveFile.exists()) {
                                console.log("File alredy exists:", fileName);
                                continue;
                        }

                        console.log(`Downloading: ${fileName}...`);
                        try {
                                const resFile = await fetch(
                                        BASE_URL + fileName,
                                );
                                if (resFile.ok) {
                                        saveFile.write(
                                                await resFile.arrayBuffer(),
                                        );
                                }
                        } catch (err: any) {
                                console.error(
                                        `Error: could not donwload ${fileName}:`,
                                        err.message,
                                );
                                console.log(err);
                        }
                }
        } catch (error: any) {
                console.error(
                        "Error: could not execute the Scrapper:",
                        error.message,
                );
        }
        try {
                await Bun.write(
                        "diários.zip",
                        new Bun.Archive(
                                await list_files_and_get_data(OUTPUT_DIR),
                                { compress: "gzip", level: 6 },
                        ),
                );
                console.log("Pasta compactada com sucesso!");
        } catch (error) {
                console.error("Erro ao compactar a pasta:", error);
        }
}

async function list_files_and_get_data(path: string): Promise<ArchiveInput> {
        const files_data: ArchiveInput = {};
        // Aguarda a leitura do diretório de forma assíncrona, mas linear
        if (!(await fs.exists(path))) {
                console.error("Directory doesn't exist");
                return {};
        }
        const files = await fs.readdir(path, { withFileTypes: true });

        for (const file of files) {
                if (file.isFile()) {
                        files_data[file.parentPath + file.name] =
                                await Bun.file(
                                        join(file.parentPath, file.name),
                                ).arrayBuffer();
                }
        }

        return files_data;
}
// console.log(await list_files_and_get_data(OUTPUT_DIR));

get_recent_files(30);