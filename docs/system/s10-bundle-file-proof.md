### Source-bundle tests follow plain-file citation proof

The source-bundle tests expect what the response gate now does with a plain file
citation: without an exact current local match it is omitted, and the display
basename and the search source path go with it. A collection that cannot supply a
complete snapshot never proves a plain file, so its drawers show no source file.
Thread, range and text of a readable bundle are unchanged. The omission of the
placeholder source file and search source path of a drawer without a source is
existing response behaviour; the tests now follow it and no product code changed.
