package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.dto.CardListDTO;
import com.qianjiangsoft.graphapi.service.CardService;
import com.qianjiangsoft.graphcore.param.card.CreateCardParam;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

import static com.qianjiangsoft.graphcore.constants.CardKeyConstants.batchCardKey;
import static com.qianjiangsoft.graphcore.constants.CardKeyConstants.batchCardName;

/**
 * @author ulmarr8861
 * @date 2022/3/18
 */
@RequestMapping("/card")
@RestController
@RequiredArgsConstructor
public class CardController {

    private final CardService cardService;


    @GetMapping("/selectCardList")
    public List<CardListDTO> selectCardList(){
        return cardService.selectCardList();
    }

    @PostMapping("/createBatchCard")
    public void createCard(@RequestBody CreateCardParam param) {
        param.setName(batchCardName);
        param.setKey(batchCardKey);
        cardService.createCard(param);
    }

    @PostMapping("/deleteAll")
    public void deleteAll() {
        cardService.deleteAll();
    }
}
