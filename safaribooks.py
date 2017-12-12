import os
import sys
import json
import shutil
import logging
import argparse
import requests
from lxml import html
from html import escape
from random import random
from urllib.parse import urljoin, urlsplit
from multiprocessing import Process, Queue, Value


PATH = os.path.dirname(os.path.realpath(__file__))
COOKIES_FILE = os.path.join(PATH, "cookies.json")


class Display:
    BASE_FORMAT = logging.Formatter(
        fmt="[%(asctime)s] %(message)s",
        datefmt="%d/%b/%Y %H:%M:%S"
    )

    SH_DEFAULT = "\033[0m"
    SH_YELLOW = "\033[33m"
    SH_BG_RED = "\033[41m"
    SH_BG_YELLOW = "\033[43m"

    def __init__(self):
        self.columns, _ = shutil.get_terminal_size()

        self.logger = logging.getLogger("SafariBooks")
        self.logger.setLevel(logging.INFO)
        logs_handler = logging.FileHandler(filename=os.path.join(PATH, "info.log"))
        logs_handler.setFormatter(self.BASE_FORMAT)
        logs_handler.setLevel(logging.INFO)
        self.logger.addHandler(logs_handler)

        self.logger.info("** Welcome to SafariBooks! **")

        self.book_ad_info = False
        self.css_ad_info = Value("i", 0)
        self.images_ad_info = Value("i", 0)
        self.in_error = False

        self.state_status = Value("i", 0)
        sys.excepthook = self.unhandled_exception

    def log(self, message):
        self.logger.info(str(message))

    def out(self, put):
        sys.stdout.write("\r" + " " * self.columns + "\r" + put + "\n")

    def info(self, message, state=False):
        self.log(message)
        output = (self.SH_YELLOW + "[*]" + self.SH_DEFAULT if not state else
                  self.SH_BG_YELLOW + "[-]" + self.SH_DEFAULT) + " %s" % message
        self.out(output)

    def error(self, error):
        if not self.in_error:
            self.in_error = True

        self.log(error)
        output = self.SH_BG_RED + "[#]" + self.SH_DEFAULT + " %s" % error
        self.out(output)

    def exit(self, error):
        self.error(str(error))
        output = (self.SH_YELLOW + "[+]" + self.SH_DEFAULT +
                  " Please delete all the `<BOOK NAME>/OEBPS/*.xhtml`"
                  " files and restart the program.")
        self.out(output)

        output = self.SH_BG_RED + "[!]" + self.SH_DEFAULT + " Aborting..."
        self.out(output)
        sys.exit(128)

    def unhandled_exception(self, _, o, __):
        self.exit("Unhandled Exception: %s (type: %s)" % (o, o.__class__.__name__))

    def intro(self):
        output = self.SH_YELLOW + """
       ____     ___         _ 
      / __/__ _/ _/__ _____(_)
     _\ \/ _ `/ _/ _ `/ __/ / 
    /___/\_,_/_/ \_,_/_/ /_/  
      / _ )___  ___  / /__ ___
     / _  / _ \/ _ \/  '_/(_-<
    /____/\___/\___/_/\_\/___/
""" + self.SH_DEFAULT
        output += "\n" + "~" * (self.columns // 2)
        self.out(output)

    def parse_description(self, desc):
        try:
            return html.fromstring(desc).text_content()

        except (html.etree.ParseError, html.etree.ParserError) as e:
            self.log("Error parsing the description: %s" % e)
            return "n/d"

    def book_info(self, info):
        description = self.parse_description(info["description"]).replace("\n", " ")
        for t in [
            ("Title", info["title"]), ("Authors", ", ".join(aut["name"] for aut in info["authors"])),
            ("Identifier", info["identifier"]), ("ISBN", info["isbn"]),
            ("Publishers", ", ".join(pub["name"] for pub in info["publishers"])),
            ("Rights", info["rights"]),
            ("Description", description[:500] + "..." if len(description) >= 500 else description),
            ("URL", info["web_url"])
        ]:
            self.info("{0}: {1}".format(t[0], t[1]), True)

    def state(self, origin, done):
        progress = int(done * 100 / origin)
        bar = int(progress * (self.columns - 11) / 100)
        if self.state_status.value < progress:
            self.state_status.value = progress
            sys.stdout.write(
                "\r    " + self.SH_BG_YELLOW + "[" + ("#" * bar).ljust(self.columns - 11, "-") + "]" +
                self.SH_DEFAULT + ("%4s" % progress) + "%" + ("\n" if progress == 100 else "")
            )

    def done(self, epub_file):
        self.info("Done: %s\n\n"
                  "    If you like it, please * this project on GitHub to make it known:\n"
                  "        https://github.com/lorenzodifuccia/safaribooks\n"
                  "    e don't forget to renew your Safari Books Online subscription:\n"
                  "        https://www.safaribooksonline.com/signup/\n\n" % epub_file +
                  self.SH_BG_RED + "[!]" + self.SH_DEFAULT + " Bye!!")

    @staticmethod
    def api_error(response):
        message = "API: "
        if "detail" in response and "Not found" in response["detail"]:
            message += "book's not present in Safari Books Online.\n" \
                       "    The book identifier are the digits that you can find in the URL:\n" \
                       "    `https://www.safaribooksonline.com/library/view/book-name/XXXXXXXXXXXXX/`"

        else:
            os.remove(COOKIES_FILE)
            message += "Out-of-Session%s.\n" % (" (%s)" % response["detail"]) if "detail" in response else "" +\
                       Display.SH_YELLOW + "[+]" + Display.SH_DEFAULT + \
                       " Use the `--cred` option in order to perform the auth login to Safari Books Online."

        return message


class SafariBooks:

    HEADERS = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "cache-control": "no-cache",
        "cookie": "",
        "pragma": "no-cache",
        "referer": "https://www.safaribooksonline.com/home/",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/62.0.3202.94 Safari/537.36"
    }

    BASE_URL = "https://www.safaribooksonline.com"
    LOGIN_URL = BASE_URL + "/accounts/login/"
    API_TEMPLATE = BASE_URL + "/api/v1/book/{0}/"

    BASE_01_HTML = "<!DOCTYPE html>\n" \
                   "<html lang=\"en\" xml:lang=\"en\" xmlns=\"http://www.w3.org/1999/xhtml\"" \
                   " xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"" \
                   " xsi:schemaLocation=\"http://www.w3.org/2002/06/xhtml2/" \
                   " http://www.w3.org/MarkUp/SCHEMA/xhtml2.xsd\"" \
                   " xmlns:epub=\"http://www.idpf.org/2007/ops\">\n" \
                   "<head>\n" \
                   "{0}\n" \
                   "<style type=\"text/css\">" \
                   "body{{background-color:#fbfbfb!important;margin:1em;}}" \
                   "#sbo-rt-content *{{text-indent:0pt!important;}}"

    KINDLE_HTML = "#sbo-rt-content *{{word-wrap:break-word!important;" \
                  "word-break:break-word!important;}}#sbo-rt-content table,#sbo-rt-content pre" \
                  "{{overflow-x:unset!important;overflow:unset!important;" \
                  "overflow-y:unset!important;white-space:pre-wrap!important;}}"

    BASE_02_HTML = "</style>" \
                   "</head>\n" \
                   "<body>{1}</body>\n</html>"

    CONTAINER_XML = "<?xml version=\"1.0\"?>" \
                    "<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">" \
                    "<rootfiles>" \
                    "<rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\" />" \
                    "</rootfiles>" \
                    "</container>"

    # Format: ID, Title, Authors, Description, Subjects, Publisher, Rights, CoverId, MANIFEST, SPINE, CoverUrl
    CONTENT_OPF = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" \
                  "<package xmlns=\"http://www.idpf.org/2007/opf\" unique-identifier=\"bookid\" version=\"2.0\" >\n" \
                  "<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\" " \
                  " xmlns:opf=\"http://www.idpf.org/2007/opf\">\n"\
                  "<dc:title>{1}</dc:title>\n" \
                  "{2}\n" \
                  "<dc:description>{3}</dc:description>\n" \
                  "{4}" \
                  "<dc:publisher>{5}</dc:publisher>\n" \
                  "<dc:rights>{6}</dc:rights>\n" \
                  "<dc:language>en-US</dc:language>\n" \
                  "<dc:identifier id=\"bookid\">{0}</dc:identifier>\n" \
                  "<meta name=\"cover\" content=\"{7}\"/>\n" \
                  "</metadata>\n" \
                  "<manifest>\n" \
                  "<item id=\"ncx\" href=\"toc.ncx\" media-type=\"application/x-dtbncx+xml\" />\n" \
                  "{8}\n" \
                  "</manifest>\n" \
                  "<spine toc=\"ncx\">\n{9}</spine>\n" \
                  "<guide><reference href=\"{10}\" title=\"Cover\" type=\"cover\" /></guide>\n" \
                  "</package>"

    # Format: ID, Depth, Title, Author, NAVMAP
    TOC_NCX = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\" ?>" \
              "<!DOCTYPE ncx PUBLIC \"-//NISO//DTD ncx 2005-1//EN\"" \
              " \"http://www.daisy.org/z3986/2005/ncx-2005-1.dtd\">" \
              "<ncx xmlns=\"http://www.daisy.org/z3986/2005/ncx/\" version=\"2005-1\">" \
              "<head>" \
              "<meta content=\"ID:ISBN:{0}\" name=\"dtb:uid\"/>" \
              "<meta content=\"{1}\" name=\"dtb:depth\"/>" \
              "<meta content=\"0\" name=\"dtb:totalPageCount\"/>" \
              "<meta content=\"0\" name=\"dtb:maxPageNumber\"/>" \
              "</head>" \
              "<docTitle><text>{2}</text></docTitle>" \
              "<docAuthor><text>{3}</text></docAuthor>" \
              "<navMap>{4}</navMap>" \
              "</ncx>"

    def __init__(self, args):
        self.args = args
        self.display = Display()
        self.display.intro()

        self.cookies = {}

        if not args.cred:
            if not os.path.isfile(COOKIES_FILE):
                self.display.exit("Login: unable to find cookies file.\n"
                                  "    Please use the --cred option to perform the login.")

            self.cookies = json.load(open(COOKIES_FILE))

        else:
            self.display.info("Logging into Safari Books Online...", state=True)
            self.do_login(*[c.replace("'", "").replace('"', "") for c in args.cred])
            if not args.no_cookies:
                json.dump(self.cookies, open(COOKIES_FILE, "w"))

        self.book_id = args.bookid
        self.api_url = self.API_TEMPLATE.format(self.book_id)
        self.book_info = self.get_book_info()
        self.display.book_info(self.book_info)
        self.book_chapters = self.get_book_chapters()
        self.display.info("Found %s chapters!" % len(self.book_chapters))
        self.chapters_queue = self.book_chapters[:]

        self.book_title = self.book_info["title"]
        self.base_url = self.book_info["web_url"]

        self.BOOK_PATH = os.path.join(PATH, self.book_title)
        self.create_dirs()
        self.display.info("Output directory:\n    %s" % self.BOOK_PATH)

        self.chapter_title = ""
        self.filename = ""
        self.css = []
        self.images = []

        self.display.info("Downloading book contents...", state=True)
        self.BASE_HTML = self.BASE_01_HTML + (self.KINDLE_HTML if not args.no_kindle else "") + self.BASE_02_HTML
        self.get()

        self.css_path = ""
        self.images_path = ""
        self.cover = ""

        self.display.info("Downloading book CSSs...", state=True)
        self.collect_css()
        self.display.info("Downloading book images...", state=True)
        self.collect_images()

        self.display.info("Creating EPUB file...", state=True)
        self.create_epub()

        if not args.no_cookies:
            json.dump(self.cookies, open(COOKIES_FILE, "w"))

        self.display.done(self.book_title + ".epub")

        if not self.display.in_error and not args.log:
            os.remove(os.path.join(PATH, "info.log"))

        sys.exit(0)

    def return_cookies(self):
        return " ".join(["{0}={1};".format(k, v) for k, v in self.cookies.items()])

    def return_headers(self, url):
        if "safaribooksonline" in urlsplit(url).netloc:
            self.HEADERS["cookie"] = self.return_cookies()

        else:
            self.HEADERS["cookie"] = ""

        return self.HEADERS

    def update_cookies(self, jar):
        for cookie in jar:
            self.cookies.update({
                cookie.name: cookie.value
            })

    def requests_provider(self, url, post=False, data=None, update_cookies=True, **kwargs):
        try:
            response = getattr(requests, "post" if post else "get")(
                url,
                headers=self.return_headers(url),
                data=data,
                **kwargs
            )

        except (requests.ConnectionError, requests.ConnectTimeout, requests.RequestException) as request_exception:
            self.display.error(str(request_exception))
            return 0

        if update_cookies:
            self.update_cookies(response.cookies)

        return response

    def do_login(self, email, password):
        response = self.requests_provider(self.BASE_URL)
        if response == 0:
            self.display.exit("Login: unable to reach Safari Books Online. Try again...")

        csrf = []
        try:
            csrf = html.fromstring(response.text).xpath("//input[@name='csrfmiddlewaretoken'][@value]")

        except (html.etree.ParseError, html.etree.ParserError) as parsing_error:
            self.display.error(parsing_error)
            self.display.exit(
                "Login: error trying to parse the home of Safari Books Online."
            )

        if not len(csrf):
            self.display.exit("Login: no CSRF Token found in the page."
                              " Unable to continue the login."
                              " Try again...")

        csrf = csrf[0].attrib["value"]
        response = self.requests_provider(
            self.LOGIN_URL,
            post=True,
            data=(
                ("csrfmiddlewaretoken", ""), ("csrfmiddlewaretoken", csrf),
                ("email", email), ("password1", password),
                ("is_login_form", "true"), ("leaveblank", ""),
                ("dontchange", "http://")
            ),
            allow_redirects=False
        )

        if response == 0:
            self.display.exit("Login: unable to perform auth to Safari Books Online.\n    Try again...")

        if response.status_code != 302:
            try:
                error_page = html.fromstring(response.text)
                errors_message = error_page.xpath("//ul[@class='errorlist']//li/text()")
                recaptcha = error_page.xpath("//div[@class='g-recaptcha']")
                messages = (["    `%s`" % error for error in errors_message
                            if "password" in error or "email" in error] if len(errors_message) else []) +\
                           (["    `ReCaptcha required (wait or do logout from the website).`"] if len(recaptcha) else[])
                self.display.exit("Login: unable to perform auth login to Safari Books Online.\n" +
                                  self.display.SH_YELLOW + "[*]" + self.display.SH_DEFAULT + " Details:\n"
                                  "%s" % "\n".join(messages if len(messages) else ["    Unexpected error!"]))
            except (html.etree.ParseError, html.etree.ParserError) as parsing_error:
                self.display.error(parsing_error)
                self.display.exit(
                    "Login: your login went wrong and it encountered in an error"
                    " trying to parse the login details of Safari Books Online. Try again..."
                )

    def get_book_info(self):
        response = self.requests_provider(self.api_url)
        if response == 0:
            self.display.exit("API: unable to retrieve book info.")

        response = response.json()
        if not isinstance(response, dict) or len(response.keys()) == 1:
            self.display.exit(self.display.api_error(response))

        if "last_chapter_read" in response:
            del response["last_chapter_read"]

        return response

    def get_book_chapters(self, page=0):
        response = self.requests_provider(urljoin(self.api_url, "chapter/" + ("" if not page else "?page=%s" % page)))
        if response == 0:
            self.display.exit("API: unable to retrieve book chapters.")

        response = response.json()

        if not isinstance(response, dict) or len(response.keys()) == 1:
            self.display.exit(self.display.api_error(response))

        if "results" not in response or not len(response["results"]):
            self.display.exit("API: unable to retrieve book chapters.")

        result = []
        result.extend([c for c in response["results"] if "cover." in c["filename"]])
        for c in result:
            del response["results"][response["results"].index(c)]

        result += response["results"]   
        return result + (self.get_book_chapters(page + 1) if response["next"] else [])

    def get_html(self, url):
        response = self.requests_provider(url)
        if response == 0:
            self.display.exit(
                "Crawler: error trying to retrieve this page: %s (%s)\n    From: %s" %
                (self.filename, self.chapter_title, url)
            )

        root = None
        try:
            root = html.fromstring(response.text, base_url=self.BASE_URL)

        except (html.etree.ParseError, html.etree.ParserError) as parsing_error:
            self.display.error(parsing_error)
            self.display.exit(
                "Crawler: error trying to parse this page: %s (%s)\n    From: %s" %
                (self.filename, self.chapter_title, url)
            )

        return root

    def link_replace(self, link):
        if link[0] == "/" and ("cover" in link or "images" in link or "graphics" in link
                               or link[-3:] in ["jpg", "jpeg", "png"]):
            self.images.append(link)
            self.display.log("Crawler: found a new image at %s" % link)
            image = link.split("/")[-1]
            return "Images/" + image

        elif link[0] not in ["/", "h"]:
            return link.replace(".html", ".xhtml")

        return link

    def parse_html(self, root):
        if random() > 0.5:
            if len(root.xpath("//div[@class='controls']/a/text()")):
                self.display.exit(self.display.api_error(" "))

        book_content = root.xpath("//div[@id='sbo-rt-content']")
        if not len(book_content):
            self.display.exit(
                "Parser: book content's corrupted or not present: %s (%s)" %
                (self.filename, self.chapter_title)
            )

        page_css = ""
        stylesheet_links = root.xpath("//link[@rel='stylesheet']")
        if len(stylesheet_links):
            stylesheet_count = 0
            for s in stylesheet_links:
                css_url = urljoin("https:", s.attrib["href"]) if s.attrib["href"][:2] == "//" \
                    else urljoin(self.base_url, s.attrib["href"])

                if css_url not in self.css:
                    self.css.append(css_url)
                    self.display.log("Crawler: found a new CSS at %s" % css_url)

                stylesheet_count += 1
                page_css += "<link href=\"Styles/Style{0:0>2}.css\" " \
                            "rel=\"stylesheet\" type=\"text/css\" />\n".format(stylesheet_count)

        stylesheets = root.xpath("//style")
        if len(stylesheets):
            for css in stylesheets:
                if "data-template" in css.attrib and len(css.attrib["data-template"]):
                    css.text = css.attrib["data-template"]
                    del css.attrib["data-template"]

                try:
                    page_css += html.tostring(css, method="xml", encoding='unicode') + "\n"

                except (html.etree.ParseError, html.etree.ParserError) as parsing_error:
                    self.display.error(parsing_error)
                    self.display.exit(
                        "Parser: error trying to parse one CSS found in this page: %s (%s)" %
                        (self.filename, self.chapter_title)
                    )

        book_content = book_content[0]
        book_content.rewrite_links(self.link_replace)

        xhtml = None
        try:
            xhtml = html.tostring(book_content, method="xml", encoding='unicode')

        except (html.etree.ParseError, html.etree.ParserError) as parsing_error:
            self.display.error(parsing_error)
            self.display.exit(
                "Parser: error trying to parse HTML of this page: %s (%s)" %
                (self.filename, self.chapter_title)
            )

        return page_css, xhtml

    def create_dirs(self):
        if os.path.isdir(self.BOOK_PATH):
            self.display.log("Book directory already exists: %s" % self.book_title)

        else:
            os.makedirs(self.BOOK_PATH)

        oebps = os.path.join(self.BOOK_PATH, "OEBPS")
        if not os.path.isdir(oebps):
            self.display.book_ad_info = True
            os.makedirs(oebps)

    def save_page_html(self, contents):
        self.filename = self.filename.replace(".html", ".xhtml")
        open(os.path.join(self.BOOK_PATH, "OEBPS", self.filename), "w")\
            .write(self.BASE_HTML.format(contents[0], contents[1]))
        self.display.log("Created: %s" % self.filename)

    def get(self):
        if not len(self.chapters_queue):
            return

        next_chapter = self.chapters_queue.pop(0)
        self.chapter_title = next_chapter["title"]
        self.filename = next_chapter["filename"]

        if os.path.isfile(os.path.join(self.BOOK_PATH, "OEBPS", self.filename.replace(".html", ".xhtml"))):
            if not self.display.book_ad_info and \
                    next_chapter not in self.book_chapters[:self.book_chapters.index(next_chapter)]:
                self.display.info("File `%s` already exists.\n"
                                  "    If you want to download again all the book%s,\n"
                                  "    please delete the `<BOOK NAME>/OEBPS/*.xhtml` files and restart the program." %
                                  (self.filename.replace(".html", ".xhtml"),
                                   " (especially because you selected the `--no-kindle` option)" if self.args.no_kindle
                                   else ""))
                self.display.book_ad_info = 1

        else:
            self.save_page_html(self.parse_html(self.get_html(urljoin(self.base_url, self.filename))))

        self.display.state(len(self.book_chapters), len(self.book_chapters) - len(self.chapters_queue))
        self.get()

    def _thread_download_css(self, url, done_queue):
        css_file = os.path.join(self.css_path, "Style{0:0>2}.css".format(self.css.index(url)))
        if os.path.isfile(css_file):
            if not self.display.css_ad_info.value and url not in self.css[:self.css.index(url)]:
                self.display.info("File `%s` already exists.\n"
                                  "    If you want to download again all the CSSs,\n"
                                  "    please delete the `<BOOK NAME>/OEBPS/*.xhtml` and `<BOOK NAME>/OEBPS/Styles/*`"
                                  " files and restart the program." %
                                  css_file)
                self.display.css_ad_info.value = 1

        else:
            response = self.requests_provider(url, update_cookies=False)
            if response == 0:
                self.display.error("Error trying to retrieve this CSS: %s\n    From: %s" % (css_file, url))

            with open(css_file, 'wb') as s:
                for chunk in response.iter_content(1024):
                    s.write(chunk)

        done_queue.put(1)
        self.display.state(len(self.css), done_queue.qsize())

    def _thread_download_images(self, url, done_queue):
        image_name = url.split("/")[-1]
        image_path = os.path.join(self.images_path, image_name)
        if os.path.isfile(image_path):
            if not self.display.images_ad_info.value and url not in self.images[:self.images.index(url)]:
                self.display.info("File `%s` already exists.\n"
                                  "    If you want to download again all the images,\n"
                                  "    please delete the `<BOOK NAME>/OEBPS/*.xhtml` and `<BOOK NAME>/OEBPS/Images/*`"
                                  " files and restart the program." %
                                  image_name)
                self.display.images_ad_info.value = 1

        else:
            response = self.requests_provider(urljoin(self.BASE_URL, url),
                                              update_cookies=False,
                                              stream=True)
            if response == 0:
                self.display.error("Error trying to retrieve this image: %s\n    From: %s" % (image_name, url))

            with open(image_path, 'wb') as img:
                for chunk in response.iter_content(1024):
                    img.write(chunk)

        done_queue.put(1)
        self.display.state(len(self.images), done_queue.qsize())

    def _start_multiprocessing(self, operation, full_queue, done_queue=None):
        if not done_queue:
            done_queue = Queue(0)

        if len(full_queue) > 5:
            for i in range(0, len(full_queue), 5):
                self._start_multiprocessing(operation, full_queue[i:i+5], done_queue)

        else:
            process_queue = [Process(target=operation, args=(arg, done_queue)) for arg in full_queue]
            for proc in process_queue:
                proc.start()

            for proc in process_queue:
                proc.join()

    def collect_css(self):
        self.css_path = os.path.join(self.BOOK_PATH, "OEBPS", "Styles")
        if os.path.isdir(self.css_path):
            self.display.log("CSSs directory already exists: %s" % self.css_path)

        else:
            os.makedirs(self.css_path)
            self.display.css_ad_info.value = 1

        self.display.state_status.value = -1
        self._start_multiprocessing(self._thread_download_css, self.css)

    def collect_images(self):
        self.images_path = os.path.join(self.BOOK_PATH, "OEBPS", "Images")
        if os.path.isdir(self.images_path):
            self.display.log("Images directory already exists: %s" % self.images_path)

        else:
            os.makedirs(self.images_path)
            self.display.images_ad_info.value = 1

        if self.display.book_ad_info == 1:
            self.display.info("Some of the book contents were already downloaded.\n"
                              "    If you want to be sure that all the images will be downloaded,\n"
                              "    please delete the `<BOOK NAME>/OEBPS/*.xhtml` files and restart the program.")

        self.display.state_status.value = -1
        self._start_multiprocessing(self._thread_download_images, self.images)

    def create_content_opf(self):
        self.cover = self.images[0] if len(self.images) else ""
        self.css = next(os.walk(self.css_path))[2]
        self.images = next(os.walk(self.images_path))[2]

        manifest = []
        spine = []
        for c in self.book_chapters:
            c["filename"] = c["filename"].replace(".html", ".xhtml")
            item_id = escape("".join(c["filename"].split(".")[:-1]))
            manifest.append("<item id=\"{0}\" href=\"{1}\" media-type=\"application/xhtml+xml\" />".format(
                item_id, c["filename"]
            ))
            spine.append("<itemref idref=\"{0}\"/>".format(item_id))

        alt_cover_id = False
        for i in self.images:
            dot_split = i.split(".")
            head = "img_" + escape("".join(dot_split[:-1]))
            extension = dot_split[-1]
            manifest.append("<item id=\"{0}\" href=\"Images/{1}\" media-type=\"image/{2}\" />".format(
                head, i, "jpeg" if "jp" in extension else extension
            ))

            if not alt_cover_id:
                alt_cover_id = head

        for i in range(1, len(self.css) + 1):
            manifest.append("<item id=\"style_{0:0>2}\" href=\"Styles/Style{0:0>2}.css\" "
                            "media-type=\"text/css\" />".format(i))

        authors = "\n".join("<dc:creator opf:file-as=\"{0}\" opf:role=\"aut\">{0}</dc:creator>".format(
            escape(aut["name"])
        ) for aut in self.book_info["authors"])

        subjects = "\n".join("<dc:subject>{0}</dc:subject>".format(escape(sub["name"]))
                             for sub in self.book_info["subjects"])

        return self.CONTENT_OPF.format(
            (self.book_info["isbn"] if len(self.book_info["isbn"]) else self.book_id),
            escape(self.book_title),
            authors,
            escape(self.book_info["description"]),
            subjects,
            ", ".join(escape(pub["name"]) for pub in self.book_info["publishers"]),
            escape(self.book_info["rights"]),
            self.cover if self.cover else alt_cover_id,
            "\n".join(manifest),
            "\n".join(spine),
            self.book_chapters[0]["filename"].replace(".html", ".xhtml")
        )

    @staticmethod
    def parse_toc(l, c=0, mx=0):
        r = ""
        for cc in l:
            c += 1
            if int(cc["depth"]) > mx:
                mx = int(cc["depth"])

            r += "<navPoint id=\"{0}\" playOrder=\"{1}\">" \
                 "<navLabel><text>{2}</text></navLabel>" \
                 "<content src=\"{3}\"/>".format(
                    cc["fragment"] if len(cc["fragment"]) else cc["id"], c,
                    escape(cc["label"]), cc["href"].replace(".html", ".xhtml")
                 )

            if cc["children"]:
                sr, c, mx = SafariBooks.parse_toc(cc["children"], c, mx)
                r += sr

            r += "</navPoint>\n"

        return r, c, mx

    def create_toc(self):
        response = self.requests_provider(urljoin(self.api_url, "toc/"))
        if response == 0:
            self.display.exit("API: unable to retrieve book chapters. "
                              "Don't delete any files, just run again this program"
                              " in order to complete the `.epub` creation!")

        response = response.json()

        if not isinstance(response, list) and len(response.keys()) == 1:
            self.display.exit(
                self.display.api_error(response) +
                " Don't delete any files, just run again this program"
                " in order to complete the `.epub` creation!"
            )

        navmap, _, max_depth = self.parse_toc(response)
        return self.TOC_NCX.format(
            (self.book_info["isbn"] if len(self.book_info["isbn"]) else self.book_id),
            max_depth,
            self.book_title,
            ", ".join(aut["name"] for aut in self.book_info["authors"]),
            navmap
        )

    def create_epub(self):
        open(os.path.join(self.BOOK_PATH, "mimetype"), "w").write("application/epub+zip")
        meta_info = os.path.join(self.BOOK_PATH, "META-INF")
        if os.path.isdir(meta_info):
            self.display.log("META-INF directory already exists: %s" % meta_info)

        else:
            os.makedirs(meta_info)

        open(os.path.join(meta_info, "container.xml"), "w").write(self.CONTAINER_XML)
        open(os.path.join(self.BOOK_PATH, "OEBPS", "content.opf"), "w").write(self.create_content_opf())
        open(os.path.join(self.BOOK_PATH, "OEBPS", "toc.ncx"), "w").write(self.create_toc())

        zip_file = os.path.join(self.BOOK_PATH, self.book_title)
        if os.path.isfile(zip_file + ".epub"):
            os.remove(zip_file + ".epub")
        if os.path.isfile(zip_file + ".zip"):
            os.remove(zip_file + ".zip")
        shutil.make_archive(zip_file, 'zip', self.BOOK_PATH)
        os.rename(zip_file + ".zip", zip_file + ".epub")


# MAIN
arguments = argparse.ArgumentParser(prog="safaribooks.py",
                                    description="Download and generate an EPUB of your favorite books"
                                                " from Safari Books Online.",
                                    add_help=False,
                                    allow_abbrev=False)

arguments.add_argument(
    "--cred", metavar="<EMAIL:PASS>", default=False,
    help="Credentials used to perform the auth login on Safari Books Online."
         " Es. ` --cred \"account_mail@mail.com:password01\" `."
)
arguments.add_argument(
    "--no-cookies", dest="no_cookies", action='store_true',
    help="Prevent your session data to be saved into `cookies.json` file."
)
arguments.add_argument(
    "--no-kindle", dest="no_kindle", action='store_true',
    help="Remove some CSS rules that block overflow on `table` and `pre` elements."
         " Use this option if you're not going to export the EPUB to E-Readers like Amazon Kindle."
)
arguments.add_argument(
    "--preserve-log", dest="log", action='store_true', help="Leave the `info.log` file even if there isn't any error."
)
arguments.add_argument("--help", action="help", default=argparse.SUPPRESS, help='Show this help message.')

arguments.add_argument(
    "bookid", metavar='<BOOK ID>',
    help="Book digits ID that you want to download. You can find it in the URL (X-es):"
         " `https://www.safaribooksonline.com/library/view/book-name/XXXXXXXXXXXXX/`"
)

args_parsed = arguments.parse_args()

if args_parsed.cred:
    cred = args_parsed.cred.split(":")
    if len(cred) != 2 or "@" not in cred[0]:
        arguments.error("invalid credential: %s" % args_parsed.cred)

    args_parsed.cred = cred

else:
    if args_parsed.no_cookies:
        arguments.error("invalid option: `--no-cookies` is valid only if you use the `--cred` option")

if not args_parsed.bookid.isdigit():
    arguments.error("invalid book id: %s" % args_parsed.bookid)

SafariBooks(args_parsed)