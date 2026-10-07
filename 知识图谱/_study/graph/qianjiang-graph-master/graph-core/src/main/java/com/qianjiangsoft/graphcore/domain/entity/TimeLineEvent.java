package com.qianjiangsoft.graphcore.domain.entity;

import lombok.Data;
import org.influxdb.annotation.Column;

import java.util.Date;

/**
 * @author marshallwu
 * @date 2022/4/24 15:49
 */
@Data
public class TimeLineEvent {

    @Column(name = "type")
    private String type;

    @Column(name = "oppositeName")
    private String oppositeName;

    @Column(name = "oppositeAccount")
    private String oppositeAccount;

    @Column(name = "spaceId")
    private String spaceId;

    private Date time;

    @Column(name = "dataId")
    private String dataId;
}
