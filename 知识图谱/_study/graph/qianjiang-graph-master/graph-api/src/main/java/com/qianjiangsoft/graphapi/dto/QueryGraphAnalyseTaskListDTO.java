package com.qianjiangsoft.graphapi.dto;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableLogic;
import lombok.Data;

import java.util.Date;

@Data
public class QueryGraphAnalyseTaskListDTO {

    private Integer id;

    private String name;

    private String type;

    private Integer status;

    private Date createTime;

    private Date finishTime;
}