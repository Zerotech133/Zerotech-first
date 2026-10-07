package com.qianjiangsoft.graphcore.domain.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import lombok.AllArgsConstructor;
import lombok.Data;

import java.util.Date;

@Data
@AllArgsConstructor
public class GraphSpace {

    @TableId
    private String id;

    private String name;

    private Date createTime;

    private Date updateTime;

    private Date lastVisitedTime;

    private Integer activeFlag;

    private Date lastSavedTime;

    public GraphSpace(String id, String name) {
        this.id = id;
        this.name = name;
    }
}