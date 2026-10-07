package com.qianjiangsoft.graphcore.exception;

import com.qianjiangsoft.graphcore.constants.ResultCode;

/**
 * 业务异常
 * 
 * @author qianjiang
 */
public class BusinessException extends RuntimeException {
	private static final long serialVersionUID = 1L;

	private String code;

	private String message;

	public BusinessException(String message) {
		this.message = message;
		this.code = ResultCode.BUSINESS_ERROR.getCode();
	}

	public BusinessException(String code, String message) {
		this.message = message;
		this.code = code;
	}

	public BusinessException(int code, String message) {
		this.message = message;
		this.code = String.valueOf(code);
	}

	public BusinessException(ResultCode resultCode, String message) {
		this.message = message;
		this.code = resultCode.getCode();
	}

	public BusinessException(String message, Throwable e) {
		super(message, e);
		this.message = message;
	}

	@Override
	public String getMessage() {
		return message;
	}

	public String getCode() {
		return code;
	}
}
