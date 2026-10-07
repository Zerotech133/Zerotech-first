package com.qianjiangsoft.graphcore.constants;

import java.util.Arrays;
import java.util.List;

/**
 * 通用常量信息
 *
 * @author qianjiang
 */
public interface Constants {

	class CharacterEncoding{
		/**
		 * UTF-8 字符集
		 */
		public static final String ENCODING_UTF8 = "UTF-8";

		/**
		 * GBK 字符集
		 */
		public static final String ENCODING_GBK = "GBK";
	}


	class HttpPrefix{
		/**
		 * http请求
		 */
		public static final String HTTP = "http://";

		/**
		 * https请求
		 */
		public static final String HTTPS = "https://";
	}

	//成功失败标志
	class SuccessOrFail {

		/**
		 * 通用成功标识
		 */
		public static final String SUCCESS = "1";

		/**
		 * 通用失败标识
		 */
		public static final String FAIL = "0";


	}

	//
	class Message {
		/**
		 * 通用成功消息
		 */
		public static final String MSG_SUCCESS = "操作成功";

		/**
		 * 通用失败消息
		 */
		public static final String MSG_FAIL = "操作失败";
	}


	//登录日志相关
	class LoginInfoStatus {

		/**
		 * 登录成功
		 */
		public static final String LOGIN_SUCCESS = "Success";

		/**
		 * 登录失败
		 */
		public static final String LOGIN_FAIL = "Error";

		/**
		 * 注销
		 */
		public static final String LOGOUT = "Logout";
	}

	//验证码相关
	class Captcha{

		/**
		 * 验证码有效期（分钟）
		 */
		public static final Integer CAPTCHA_EXPIRATION = 2;
	}

	//token相关
	class JwtToken{
		/**
		 * 令牌
		 */
		public static final String TOKEN = "token";

		/**
		 * 令牌前缀
		 */
		public static final String TOKEN_PREFIX = "Bearer ";

		/**
		 * 令牌前缀
		 */
		public static final String LOGIN_USER_TOKEN_KEY = "login_user_token_key";
	}

	class Config {
		/**
		 * 资源映射路径 前缀
		 */
		public static final String RESOURCE_PREFIX = "/file";
	}

	class Date {

		public static final String YYYY = "yyyy";

		public static final String YYYY_MM = "yyyy-MM";

		public static final String YYYY_MM_DD = "yyyy-MM-dd";

		public static final String YYYYMMDDHHMMSS = "yyyyMMddHHmmss";

		public static final String YYYY_MM_DD_HH_MM_SS = "yyyy-MM-dd HH:mm:ss";

		public static final String[] parsePatterns = {
				"yyyy-MM-dd", "yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd HH:mm", "yyyy-MM",
				"yyyy/MM/dd", "yyyy/MM/dd HH:mm:ss", "yyyy/MM/dd HH:mm", "yyyy/MM",
				"yyyy.MM.dd", "yyyy.MM.dd HH:mm:ss", "yyyy.MM.dd HH:mm", "yyyy.MM" };
	}

	class FileType {
		public static final String[] DEFAULT_ALLOWED_EXTENSION = {
				// 图片
				"bmp", "gif", "jpg", "jpeg", "png",
				// word excel powerpoint
				"doc", "docx", "xls", "xlsx", "ppt", "pptx", "html", "htm", "txt",
				// 压缩文件
				"rar", "zip", "gz", "bz2",
				// pdf
				"pdf" };

		public static final String[] IMAGE_EXTENSION = { "bmp", "gif", "jpg", "jpeg", "png" };

		public static final String[] FLASH_EXTENSION = { "swf", "flv" };

		public static final String[] VIDEO_EXTENSION = { "swf", "flv", "mp3", "wav", "wma", "wmv", "mid", "avi", "mpg",
				"asf", "rm", "rmvb" };
	}

	class FileCategory{
		public static final String LIBRARY_TABLE_FILE = "library_table";
		public static final String RELATIONAL_GRAPH = "relational_graph";
		public static final String PERSPECTIVE_TABLE = "perspective_table";
	}

	class DataSourceName{
		public static final String MYSQL_PLUGINS = "mysql_plugins";
		public static final String MYSQL_PLUGINS_DATA = "mysql_plugins_data";
		public static final String MYSQL_PLUGINS_MODEL = "mysql_plugins_model";

		public static final String CLICKHOUSE_PLUGINS = "clickhouse_plugins";
		public static final String CLICKHOUSE_PLUGINS_MODEL = "clickhouse_plugins_model";
	}

	class YesOrNo{
		public static final int YES = 1;
		public static final int NO = 0;
	}

	class ExportExcelConfig{
		public static final String EXCEL_TITLE = "原始数据";
	}

	/**
	 * general : 查询
	 * complex ：复杂查询
	 * text ： 查找文本
	 * statistics : 统计查询
	 * findLoop : 查找回路
	 * findPath : 探索里的查找路径
	 * findSub : 探索里的连通性分析
	 * flowDirection : 资金净流向分析
	 */
	class SearchHistoryType{
		public static final String GENERAL = "general";
		public static final String COMPLEX = "complex";
		public static final String TEXT = "text";
		public static final String STATISTICS = "statistics";
		public static final String FIND_LOOP = "findLoop";
		public static final String FIND_PATH = "findPath";
		public static final String FIND_SUB = "findSub";
		public static final String FLAW_DIRECTION = "flowDirection";
	}

	class VertexPropertyName {
		public static final String caseInfoList = "caseInfoList";
	}

	class EdgePropertyName {
		public static final String caseId = "caseId";
	}

	class VertexConfig{
		public static final List<String> ignore_property_list = Arrays.asList("suspectFlag", "dataId");
	}
}
