package com.qianjiangsoft.graphcore.common;

import com.qianjiangsoft.graphcore.constants.Constants;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.experimental.Accessors;

import java.io.Serializable;

/**
 * 统一响应体
 *
 * @author YoCiyy
 * @date 2020-12-22
 */
@Data
@NoArgsConstructor
@Accessors(chain = true)
public class Result<T> implements Serializable {
    private static final long serialVersionUID = 1L;

	/** 数据对象 */
	private T data;

	/** 状态码 */
	private String code;

	/** 消息 */
	private String msg;

	public static <T> Result<T> success() { return new Result<T>(null, ResultCode.SUCCESS.getCode(), Constants.Message.MSG_SUCCESS); }

	public static <T> Result<T> success(T data) { return new Result<T>(data, ResultCode.SUCCESS.getCode(), Constants.Message.MSG_SUCCESS); }

	public static <T> Result<T> success(String msg) { return new Result<T>(null, ResultCode.SUCCESS.getCode(), msg); }

	public static <T> Result<T> success(String msg, T data) { return new Result<T>(data, ResultCode.SUCCESS.getCode(), msg); }

	public static <T> Result<T> error() { return new Result<T>(null, ResultCode.BUSINESS_ERROR.getCode(), Constants.Message.MSG_FAIL); }

	public static <T> Result<T> error(String msg) { return new Result<T>(null, ResultCode.BUSINESS_ERROR.getCode(), msg); }

	public static <T> Result<T> error(int code, String msg) { return new Result<T>(null, code, msg); }

	public static <T> Result<T> error(String code, String msg) { return new Result<T>(null, code, msg); }


	/**
	 * 初始化一个新创建的 R 对象
	 *
	 * @param code 状态码
	 * @param msg 返回内容
	 * @param data 数据对象
	 */
	public Result(T data, String code, String msg) {
		this.data = data;
		this.code = code;
		this.msg = msg;
	}

	public Result(ResultCode resultCode) {
		this.code = resultCode.getCode();
		this.msg = resultCode.getMsg();
	}

	public Result(T data, int code, String msg) {
		this.data = data;
		this.code = String.valueOf(code);
		this.msg = msg;
	}
}
