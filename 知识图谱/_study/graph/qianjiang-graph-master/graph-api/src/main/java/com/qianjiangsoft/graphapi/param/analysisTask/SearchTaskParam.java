package com.qianjiangsoft.graphapi.param.analysisTask;

import lombok.Data;
import org.springframework.format.annotation.DateTimeFormat;

import java.util.Date;

/**
 * @author ulmarr8861
 * @date 2022/9/5
 */
@Data
public class SearchTaskParam {
    private String type;

    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date beginTime;

    @DateTimeFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date endTime;
}
